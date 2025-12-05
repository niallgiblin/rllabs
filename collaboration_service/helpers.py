from database import models_collection, cache


# Cache for model creator lookups (TTL: 1 hour, rarely changes)
# Pre-warm cache on startup for better hit rates
_model_creator_cache_prewarmed = False

def prewarm_model_creator_cache():
    """Pre-warm cache with common models to improve hit rate"""
    global _model_creator_cache_prewarmed
    if _model_creator_cache_prewarmed or not cache:
        return
    
    try:
        # Pre-warm with first 100 models (most commonly accessed)
        models = models_collection.find({}).limit(100)
        for model in models:
            model_id = model.get("modelId")
            creator_id = model.get("creatorId")
            if model_id:
                cache_key = f"model_creator:{model_id}"
                cache.setex(cache_key, 3600, str(creator_id) if creator_id else "None")
        _model_creator_cache_prewarmed = True
    except Exception:
        pass  # Fail-open: pre-warming is optional

def get_model_creator(model_id: str) -> str:
    """
    Get creator ID for a model (call once per model)
    Uses Redis cache to avoid MongoDB query on every request.
    
    Optimized for performance:
    - Fast cache lookup (Redis is in-memory)
    - Uses MongoDB index on modelId for fast queries
    - Long TTL (1 hour) since creator rarely changes
    """
    # Try cache first
    cache_key = f"model_creator:{model_id}"
    if cache:
        try:
            cached_creator = cache.get(cache_key)
            if cached_creator:
                return cached_creator if cached_creator != "None" else None
        except Exception:
            pass  # Cache miss or error, continue to DB
    
    # Cache miss - query MongoDB (uses index on modelId)
    # Only fetch creatorId field for better performance
    model = models_collection.find_one(
        {"modelId": model_id},
        {"creatorId": 1}  # Projection: only fetch creatorId field
    )
    
    if not model:
        creator_id = None
    else:
        creator_id = model.get("creatorId")
    
    # Cache the result (1 hour TTL - creator rarely changes)
    if cache:
        try:
            cache.setex(cache_key, 3600, str(creator_id) if creator_id else "None")
        except Exception:
            pass  # Cache write failed, but we have the data
    
    return creator_id




def doc_to_response(doc, creator_id=None) -> dict:
    """
    Helper to convert MongoDB doc to response
    creator_id: pass once to avoid N+1 queries
    """
    
    # Check if author is model creator for badge
    is_creator = (creator_id == doc["authorId"]) if creator_id else False
    
    return {
        "id": str(doc["_id"]),
        "modelId": doc["modelId"],
        "content": doc["content"],
        "authorId": doc["authorId"],
        "authorName": doc["authorName"],
        "isCreator": is_creator,  # Badge flag
        "parentId": doc.get("parentId"),
        "createdAt": doc["createdAt"].isoformat(),
        "updatedAt": doc["updatedAt"].isoformat(),
        "replies": []
    }






def build_tree(comments: list[dict]) -> list[dict]:
    """
    Build nested tree from flat comments
    """
    
    # First pass: create map and initialize replies list
    comment_map = {}
    for c in comments:
        c["replies"] = []  # Initialize empty replies
        comment_map[c["id"]] = c
    
    
    # Second pass: build tree structure
    roots = []
    for comment in comments:
        
        # If comment is reply --> add to parent
        if comment["parentId"]:
            parent = comment_map.get(comment["parentId"])
            
            if parent:
                parent["replies"].append(comment)
                # If parent doesn't exist, orphaned comment --> I think this should NOT happend, 
                # but I leave this error handling just in case
            
            
        else:
            # Else --> It's top level comment 
            roots.append(comment)
    
    return roots