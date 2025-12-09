from database import models_collection, cache


_model_creator_cache_prewarmed = False

def prewarm_model_creator_cache():
    """Pre-warm cache with common models to improve hit rate"""
    global _model_creator_cache_prewarmed
    if _model_creator_cache_prewarmed or not cache:
        return
    
    try:
        models = models_collection.find({}).limit(100)
        for model in models:
            model_id = model.get("modelId")
            creator_id = model.get("creatorId")
            if model_id:
                cache_key = f"model_creator:{model_id}"
                cache.setex(cache_key, 3600, str(creator_id) if creator_id else "None")
        _model_creator_cache_prewarmed = True
    except Exception:
        pass  

def get_model_creator(model_id: str) -> str:
    """
    Get creator ID for a model (call once per model)
    Uses Redis cache to avoid MongoDB query on every request.
    
    Optimized for performance:
    - Fast cache lookup (Redis is in-memory)
    - Uses MongoDB index on modelId for fast queries
    - Long TTL (1 hour) since creator rarely changes
    """
    cache_key = f"model_creator:{model_id}"
    if cache:
        try:
            cached_creator = cache.get(cache_key)
            if cached_creator:
                return cached_creator if cached_creator != "None" else None
        except Exception:
            pass  
    
    model = models_collection.find_one(
        {"modelId": model_id},
        {"creatorId": 1}  
    )
    
    if not model:
        creator_id = None
    else:
        creator_id = model.get("creatorId")
    
    if cache:
        try:
            cache.setex(cache_key, 3600, str(creator_id) if creator_id else "None")
        except Exception:
            pass  
    
    return creator_id




def doc_to_response(doc, creator_id=None) -> dict:
    """
    Helper to convert MongoDB doc to response
    creator_id: pass once to avoid N+1 queries
    """
    
    is_creator = (creator_id == doc["authorId"]) if creator_id else False
    
    return {
        "id": str(doc["_id"]),
        "modelId": doc["modelId"],
        "content": doc["content"],
        "authorId": doc["authorId"],
        "authorName": doc["authorName"],
        "isCreator": is_creator,  
        "parentId": doc.get("parentId"),
        "createdAt": doc["createdAt"].isoformat(),
        "updatedAt": doc["updatedAt"].isoformat(),
        "replies": []
    }






def build_tree(comments: list[dict]) -> list[dict]:
    """
    Build nested tree from flat comments
    """
    
    comment_map = {}
    for c in comments:
        c["replies"] = []  
        comment_map[c["id"]] = c
    
    
    roots = []
    for comment in comments:
        
        if comment["parentId"]:
            parent = comment_map.get(comment["parentId"])
            
            if parent:
                parent["replies"].append(comment)
            
        else:
            roots.append(comment)
    
    return roots