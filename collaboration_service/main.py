"""
Collaboration Service - Main Application
========================================
Manages comments and collaboration features for models.
"""

# =============================================================================
# OBSERVABILITY SETUP (must be first, before other imports)
# =============================================================================
import os
import sys

# Add shared module to path
shared_path = os.path.join(os.path.dirname(__file__), 'shared')
if os.path.exists(shared_path) and shared_path not in sys.path:
    sys.path.insert(0, shared_path)

SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "collaboration-service")

# Initialize structured logging and tracing
try:
    from observability import setup_logging, setup_tracing, get_logger
    
    json_output = os.getenv("KUBERNETES_SERVICE_HOST") is not None
    setup_logging(service_name=SERVICE_NAME, json_output=json_output)
    setup_tracing(service_name=SERVICE_NAME)
    logger = get_logger(__name__)
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    logger.warning("Observability module not available, using basic logging")

# =============================================================================
# APPLICATION IMPORTS
# =============================================================================
from fastapi import FastAPI, HTTPException, status, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from datetime import datetime
from bson import ObjectId
from bson.errors import InvalidId
import json
import threading

from schema import CommentCreate, CommentUpdate
from helpers import doc_to_response, build_tree, get_model_creator
from database import comments_collection, cache, create_indexes
from events import publish_comment_created, start_event_subscriber

app = FastAPI(
    title="Collaboration Service",
    description="Service for managing comments and collaboration on models",
    version="1.0.0"
)


# Add CORS Middleware --> Allows browser to fetch data
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # CHNAGE THIS IF WE DEPLOY
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add Prometheus metrics
try:
    from prometheus_fastapi_instrumentator import Instrumentator
    instrumentator = Instrumentator()
    instrumentator.instrument(app).expose(app)
    logger.info("Prometheus metrics enabled")
except ImportError:
    logger.warning("prometheus-fastapi-instrumentator not available - metrics disabled")




@app.on_event("startup")
def startup():
    """
    Initialize indexes and start event subscriber on startup
    """
    
    create_indexes()
    
    # Pre-warm model creator cache for better hit rates
    from helpers import prewarm_model_creator_cache
    try:
        prewarm_model_creator_cache()
        logger.info("Model creator cache pre-warmed")
    except Exception as e:
        logger.warning(f"Failed to pre-warm cache: {e}")
    
    # Start event subscriber in background thread
    thread = threading.Thread(target=start_event_subscriber, daemon=True)
    thread.start()
    logger.info("Event subscriber started")

# Health check endpoint for Kubernetes readiness/liveness probes - Fast (no dependencies)
@app.get("/health", tags=["Monitoring"])
def health_check():
    """
    Fast health check endpoint for Kubernetes probes.
    Returns immediately without checking dependencies to avoid unnecessary database load.
    """
    return {"status": "ok"}

# Detailed health check with dependency verification
@app.get("/health/detailed", tags=["Monitoring"])
def detailed_health_check():
    """
    Detailed health check endpoint with dependency verification.
    Use this for monitoring dashboards, not for Kubernetes probes.
    """
    try:
        # Check MongoDB connectivity
        comments_collection.database.client.admin.command('ping')
        mongo_status = "online"
    except Exception as e:
        logger.error(f"MongoDB health check failed: {e}")
        mongo_status = "offline"
    
    try:
        # Check Redis connectivity
        if cache:
            cache.ping()
            redis_status = "online"
        else:
            redis_status = "offline"
    except Exception as e:
        logger.error(f"Redis health check failed: {e}")
        redis_status = "offline"
    
    overall_status = "ok" if mongo_status == "online" and redis_status == "online" else "degraded"
    
    return {
        "service_status": overall_status,
        "dependencies": {
            "mongodb": mongo_status,
            "redis": redis_status
        }
    }






@app.post("/models/{model_id}/comments", status_code=status.HTTP_201_CREATED)
def create_comment(model_id: str, comment: CommentCreate, background_tasks: BackgroundTasks):
    """
    Optimistic accept --> Trust frontend, no validation
    If user is on model page, model exists
    """
    
    now = datetime.utcnow()
    
    doc = {
        "modelId": model_id,
        "content": comment.content,
        "authorId": comment.authorId,
        "authorName": comment.authorName,
        "parentId": comment.parentId,
        "createdAt": now,
        "updatedAt": now
    }
    
    result = comments_collection.insert_one(doc)
    doc["_id"] = result.inserted_id
    
    # Invalidate cache for this model (all pages and count)
    try:
        if cache:
            # Delete all comment pages for this model (pattern matching)
            # Note: Redis doesn't support pattern delete in single command, so we delete known patterns
            # For simplicity, we'll delete the count cache and let page caches expire naturally
            cache.delete(f"comments:count:{model_id}")
            # Also try to delete common page patterns (fail-open if not found)
            for page in range(1, 11):  # Delete first 10 pages (most common)
                try:
                    cache.delete(f"comments:{model_id}:page:{page}:limit:50")
                except:
                    pass
    except Exception:
        pass  # Fail-open: cache failures don't break the service
    
    # Get creator for response
    creator_id = get_model_creator(model_id)
    response_data = doc_to_response(doc, creator_id)
    
    # Publish CommentCreated event in background (non-blocking)
    background_tasks.add_task(publish_comment_created, response_data)
    
    return response_data





@app.get("/models/{model_id}/comments")
def get_comments(model_id: str, page: int = 1, limit: int = 50):
    """
    Get comments for a model as nested tree with pagination.
    
    Pagination is done at the database level for better performance.
    Only top-level comments (no parent) are paginated; replies are always included.
    
    Performance optimizations:
    - Cached count_documents() results (counts change infrequently)
    - Cached full response (5 min TTL)
    - Single MongoDB query for all replies (not N queries)
    """
    
    cache_key = f"comments:{model_id}:page:{page}:limit:{limit}"
    count_cache_key = f"comments:count:{model_id}"
    
    # Try cache first (fail-open: if cache fails, just query DB)
    cached_result = None
    try:
        if cache:
            cached = cache.get(cache_key)
            if cached:
                cached_result = json.loads(cached)
    except Exception:
        pass  # Cache miss or error, continue to DB query
    
    if cached_result is not None:
        return cached_result
    
    # Fetch creator once for all comments (cached internally)
    creator_id = get_model_creator(model_id)
    
    # Get total count of top-level comments (for pagination metadata)
    # Cache count separately - counts change less frequently than comments
    total_top_level = None
    try:
        if cache:
            cached_count = cache.get(count_cache_key)
            if cached_count:
                total_top_level = int(cached_count)
    except Exception:
        pass  # Cache miss, will query DB
    
    if total_top_level is None:
        # Cache miss - query MongoDB (use index on modelId + parentId)
        total_top_level = comments_collection.count_documents({
            "modelId": model_id,
            "parentId": None
        })
        # Cache count for 10 minutes (counts change less frequently than comments)
        try:
            if cache:
                cache.setex(count_cache_key, 600, str(total_top_level))
        except Exception:
            pass  # Cache write failed, but we have the data
    
    # Paginate at database level: get top-level comments for this page
    skip = (page - 1) * limit
    top_level_cursor = (
        comments_collection
        .find({"modelId": model_id, "parentId": None})
        .sort("createdAt", -1)
        .skip(skip)
        .limit(limit)
    )
    top_level_comments = [doc_to_response(doc, creator_id) for doc in top_level_cursor]
    
    # Get all replies for these top-level comments (optimized: single query + in-memory filtering)
    # Instead of recursive queries (N queries where N = depth), fetch all replies once and filter
    # This is much faster for deep comment trees
    if top_level_comments:
        top_level_ids = [str(comment["id"]) for comment in top_level_comments]
        
        # Single query: Get ALL replies for this model (non-top-level comments)
        # Then filter in-memory to only those that are descendants of our top-level comments
        all_replies_cursor = comments_collection.find({
            "modelId": model_id,
            "parentId": {"$ne": None}  # All replies (non-top-level comments)
        })
        
        # Build set of valid parent IDs (starts with top-level, grows as we find replies)
        valid_parent_ids = set(top_level_ids)
        replies_in_tree = []
        
        # First pass: collect all replies
        all_replies_docs = list(all_replies_cursor)
        
        # Second pass: filter to only replies in our tree (iterative approach)
        # Keep iterating until no new replies are found (handles arbitrary depth)
        changed = True
        while changed:
            changed = False
            for reply_doc in all_replies_docs:
                reply_id = str(reply_doc["_id"])
                reply_parent_id = str(reply_doc.get("parentId", ""))
                
                # If this reply's parent is in the tree, add this reply to the tree
                if reply_parent_id in valid_parent_ids and reply_id not in valid_parent_ids:
                    replies_in_tree.append(reply_doc)
                    valid_parent_ids.add(reply_id)
                    changed = True
        
        # Convert to response format
        replies = [doc_to_response(doc, creator_id) for doc in replies_in_tree]
        
        # Combine top-level and replies, then build tree
        all_comments = top_level_comments + replies
        paginated_tree = build_tree(all_comments)
    else:
        paginated_tree = []
    
    result = {
        "data": paginated_tree,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total_top_level,
            "hasMore": skip + limit < total_top_level
        }
    }
    
    # Cache for 5 min (fail-open: if cache fails, just continue)
    try:
        if cache:
            cache.setex(cache_key, 300, json.dumps(result))
            # Also invalidate count cache when comments change (handled in create/update/delete)
    except Exception:
        pass  # Cache write failed, but we have the data
    
    return result









@app.get("/comments/{comment_id}")
def get_comment(comment_id: str):
    """
    Get a specific comment by ID
    """
    try:
        # Validate ObjectId format first - this will raise InvalidId if format is wrong
        try:
            object_id = ObjectId(comment_id)
        except InvalidId:
            raise HTTPException(status_code=400, detail="Invalid comment ID format")
        
        comment = comments_collection.find_one({"_id": object_id})
        if not comment:
            raise HTTPException(status_code=404, detail="Comment not found")
        
        # Get creator for badge
        creator_id = get_model_creator(comment["modelId"])
        return doc_to_response(comment, creator_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.put("/comments/{comment_id}")
def update_comment(comment_id: str, update: CommentUpdate):
    """
    Update comment content
    """
    
    try:
        # Validate ObjectId format first - this will raise InvalidId if format is wrong
        try:
            object_id = ObjectId(comment_id)
        except InvalidId:
            raise HTTPException(status_code=400, detail="Invalid comment ID format")
        
        # Get comment first to know which model cache to invalidate
        comment = comments_collection.find_one({"_id": object_id})
        if not comment:
            raise HTTPException(status_code=404, detail="Comment not found")
        
        result = comments_collection.update_one(
            {"_id": object_id},
            {
                "$set": {
                    "content": update.content,
                    "updatedAt": datetime.utcnow()
                }
            }
        )
        
        # Invalidate cache (all pages and count)
        try:
            if cache:
                model_id = comment['modelId']
                cache.delete(f"comments:count:{model_id}")
                # Delete common page patterns
                for page in range(1, 11):
                    try:
                        cache.delete(f"comments:{model_id}:page:{page}:limit:50")
                    except:
                        pass
        except Exception:
            pass  # Fail-open: cache failures don't break the service
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
    
    return {"message": "Comment updated"}





@app.delete("/comments/{comment_id}")
def delete_comment(comment_id: str):
    """
    Delete comment and ALL its descendants recursively
    """
    
    try:
        # Validate ObjectId format first - this will raise InvalidId if format is wrong
        try:
            object_id = ObjectId(comment_id)
        except InvalidId:
            raise HTTPException(status_code=400, detail="Invalid comment ID format")
        
        # Find comment to check if it exists
        comment = comments_collection.find_one({"_id": object_id})
        if not comment:
            raise HTTPException(status_code=404, detail="Comment not found")
        
        model_id = comment["modelId"]
        
        # Recursively collect all descendant IDs
        def get_all_descendants(parent_id):
            """Find all descendants (children, grandchildren, etc)"""
            descendants = []
            
            # Find direct children
            children = comments_collection.find({"parentId": parent_id})
            
            for child in children:
                child_id = str(child["_id"])
                descendants.append(child_id)
                # Recursively get this child's descendants
                descendants.extend(get_all_descendants(child_id))
            
            return descendants
        
        # Get all descendants
        all_descendants = get_all_descendants(comment_id)
        
        # Delete the original comment
        comments_collection.delete_one({"_id": object_id})
        
        # Delete all descendants
        if all_descendants:
            comments_collection.delete_many({
                "_id": {"$in": [ObjectId(id) for id in all_descendants]}
            })
        
        # Invalidate cache (all pages and count)
        try:
            if cache:
                cache.delete(f"comments:count:{model_id}")
                # Delete common page patterns
                for page in range(1, 11):
                    try:
                        cache.delete(f"comments:{model_id}:page:{page}:limit:50")
                    except:
                        pass
        except Exception:
            pass  # Fail-open: cache failures don't break the service
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
    
    return {"message": "Comment deleted"}





app.mount("/", StaticFiles(directory="static", html=True), name="static")