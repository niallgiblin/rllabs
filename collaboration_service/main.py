from fastapi import FastAPI, HTTPException, status
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


app = FastAPI()


# Add CORS Middleware --> Allows browser to fetch data
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # CHNAGE THIS IF WE DEPLOY
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)




@app.on_event("startup")
def startup():
    """
    Initialize indexes and start event subscriber on startup
    """
    
    create_indexes()
    
    # Start event subscriber in background thread
    thread = threading.Thread(target=start_event_subscriber, daemon=True)
    thread.start()
    print("✅ Event subscriber started")






@app.post("/models/{model_id}/comments", status_code=status.HTTP_201_CREATED)
def create_comment(model_id: str, comment: CommentCreate):
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
    
    # Invalidate cache for this model
    cache.delete(f"comments:{model_id}")
    
    # Get creator for response
    creator_id = get_model_creator(model_id)
    response_data = doc_to_response(doc, creator_id)
    
    # Publish CommentCreated event
    try:
        publish_comment_created(response_data)
    except Exception as e:
        print(f"⚠️  Failed to publish CommentCreated event: {e}")
    
    return response_data





@app.get("/models/{model_id}/comments")
def get_comments(model_id: str, page: int = 1, limit: int = 50):
    """
    Get all comments for a model as nested tree
    """
    
    cache_key = f"comments:{model_id}"
    
    # Try cache first
    cached = cache.get(cache_key)
    if cached:
        all_comments = json.loads(cached)
    else:
        # Fetch creator once for all comments
        creator_id = get_model_creator(model_id)
        
        # Cache miss --> query DB (exclude archived comments)
        cursor = comments_collection.find({
            "modelId": model_id,
            "$or": [
                {"archived": {"$exists": False}},
                {"archived": False}
            ]
        }).sort("createdAt", -1)
        comments: list[dict] = [doc_to_response(doc, creator_id) for doc in cursor]
        
        # Build tree
        all_comments = build_tree(comments)
        
        # Cache for 5 min
        cache.setex(cache_key, 300, json.dumps(all_comments))
    
    
    # Pagination (only on top-level comments)
    start = (page - 1) * limit
    end = start + limit
    paginated = all_comments[start:end]
    total = len(all_comments)
    
    return {
        "data": paginated,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "hasMore": end < total
        }
    }









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
        
        # Invalidate cache
        cache.delete(f"comments:{comment['modelId']}")
        
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
        
        # Invalidate cache to ensure fresh data on next fetch
        cache.delete(f"comments:{model_id}")
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
    
    return {"message": "Comment deleted"}





app.mount("/", StaticFiles(directory="static", html=True), name="static")