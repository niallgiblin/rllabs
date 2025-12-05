from database import models_collection


def get_model_creator(model_id: str) -> str:
    """
    Get creator ID for a model (call once per model)
    Event-driven: Creator info is cached from ModelCreated events via RabbitMQ
    """
    model = models_collection.find_one({"modelId": model_id})
    
    if not model:
        return None
    
    return model.get("creatorId")




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