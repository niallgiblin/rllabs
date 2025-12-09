from pydantic import BaseModel
from typing import Optional, List


class CommentCreate(BaseModel):
    content: str
    authorId: str
    authorName: str
    parentId: Optional[str] = None


class CommentResponse(BaseModel):
    id: str              
    modelId: str         
    content: str
    authorId: str
    authorName: str
    isCreator: bool      
    parentId: Optional[str]
    createdAt: str       
    updatedAt: str       
    replies: List[dict]  


class CommentUpdate(BaseModel):
    content: str         