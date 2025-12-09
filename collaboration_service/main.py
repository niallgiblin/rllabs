"""
Collaboration Service - Main Application
========================================
Manages comments and collaboration features for models.
"""

# Observability Setup
import os
import sys

shared_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'shared'))
if os.path.exists(shared_path) and shared_path not in sys.path:
    sys.path.insert(0, shared_path)

SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "collaboration-service")

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



from fastapi import FastAPI, HTTPException, status, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
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


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    
    from helpers import prewarm_model_creator_cache
    try:
        prewarm_model_creator_cache()
        logger.info("Model creator cache pre-warmed")
    except Exception as e:
        logger.warning(f"Failed to pre-warm cache: {e}")
    
    thread = threading.Thread(target=start_event_subscriber, daemon=True)
    thread.start()
    logger.info("Event subscriber started")

@app.get("/health", tags=["Monitoring"])
def health_check():
    """
    Fast health check endpoint for Kubernetes probes.
    Returns immediately without checking dependencies to avoid unnecessary database load.
    """
    return {"status": "ok"}

@app.get("/health/detailed", tags=["Monitoring"])
def detailed_health_check():
    """
    Detailed health check endpoint with dependency verification.
    Use this for monitoring dashboards, not for Kubernetes probes.
    """
    try:
        comments_collection.database.client.admin.command('ping')
        mongo_status = "online"
    except Exception as e:
        logger.error(f"MongoDB health check failed: {e}")
        mongo_status = "offline"
    
    try:
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
    
    try:
        if cache:
            cache.delete(f"comments:count:{model_id}")
            for page in range(1, 11): 
                try:
                    cache.delete(f"comments:{model_id}:page:{page}:limit:50")
                except:
                    pass
    except Exception:
        pass  
    
    creator_id = get_model_creator(model_id)
    response_data = doc_to_response(doc, creator_id)
    
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
    
    cached_result = None
    try:
        if cache:
            cached = cache.get(cache_key)
            if cached:
                cached_result = json.loads(cached)
    except Exception:
        pass  
    
    if cached_result is not None:
        return cached_result
    
    creator_id = get_model_creator(model_id)
    
    total_top_level = None
    try:
        if cache:
            cached_count = cache.get(count_cache_key)
            if cached_count:
                total_top_level = int(cached_count)
    except Exception:
        pass  
    
    if total_top_level is None:
        total_top_level = comments_collection.count_documents({
            "modelId": model_id,
            "parentId": None
        })
        try:
            if cache:
                cache.setex(count_cache_key, 600, str(total_top_level))
        except Exception:
            pass  
    
    skip = (page - 1) * limit
    top_level_cursor = (
        comments_collection
        .find({"modelId": model_id, "parentId": None})
        .sort("createdAt", -1)
        .skip(skip)
        .limit(limit)
    )
    top_level_comments = [doc_to_response(doc, creator_id) for doc in top_level_cursor]
    
    if top_level_comments:
        top_level_ids = [str(comment["id"]) for comment in top_level_comments]
        
        all_replies_cursor = comments_collection.find({
            "modelId": model_id,
            "parentId": {"$ne": None}  
        })
        
        valid_parent_ids = set(top_level_ids)
        replies_in_tree = []
        
        all_replies_docs = list(all_replies_cursor)
        
        changed = True
        while changed:
            changed = False
            for reply_doc in all_replies_docs:
                reply_id = str(reply_doc["_id"])
                reply_parent_id = str(reply_doc.get("parentId", ""))
                
                if reply_parent_id in valid_parent_ids and reply_id not in valid_parent_ids:
                    replies_in_tree.append(reply_doc)
                    valid_parent_ids.add(reply_id)
                    changed = True
        
        replies = [doc_to_response(doc, creator_id) for doc in replies_in_tree]
        
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
    
    try:
        if cache:
            cache.setex(cache_key, 300, json.dumps(result))
    except Exception:
        pass  
    
    return result







@app.get("/comments/{comment_id}")
def get_comment(comment_id: str):
    """
    Get a specific comment by ID
    """
    try:
        try:
            object_id = ObjectId(comment_id)
        except InvalidId:
            raise HTTPException(status_code=400, detail="Invalid comment ID format")
        
        comment = comments_collection.find_one({"_id": object_id})
        if not comment:
            raise HTTPException(status_code=404, detail="Comment not found")
        
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
        try:
            object_id = ObjectId(comment_id)
        except InvalidId:
            raise HTTPException(status_code=400, detail="Invalid comment ID format")
        
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
        
        try:
            if cache:
                model_id = comment['modelId']
                cache.delete(f"comments:count:{model_id}")
                for page in range(1, 11):
                    try:
                        cache.delete(f"comments:{model_id}:page:{page}:limit:50")
                    except:
                        pass
        except Exception:
            pass  
        
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
        try:
            object_id = ObjectId(comment_id)
        except InvalidId:
            raise HTTPException(status_code=400, detail="Invalid comment ID format")
        
        comment = comments_collection.find_one({"_id": object_id})
        if not comment:
            raise HTTPException(status_code=404, detail="Comment not found")
        
        model_id = comment["modelId"]
        
        def get_all_descendants(parent_id):
            """Find all descendants (children, grandchildren, etc)"""
            descendants = []
            
            children = comments_collection.find({"parentId": parent_id})
            
            for child in children:
                child_id = str(child["_id"])
                descendants.append(child_id)
                descendants.extend(get_all_descendants(child_id))
            
            return descendants
        
        all_descendants = get_all_descendants(comment_id)
        
        comments_collection.delete_one({"_id": object_id})
        
        if all_descendants:
            comments_collection.delete_many({
                "_id": {"$in": [ObjectId(id) for id in all_descendants]}
            })
        
        try:
            if cache:
                cache.delete(f"comments:count:{model_id}")
                for page in range(1, 11):
                    try:
                        cache.delete(f"comments:{model_id}:page:{page}:limit:50")
                    except:
                        pass
        except Exception:
            pass  
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
    
    return {"message": "Comment deleted"}