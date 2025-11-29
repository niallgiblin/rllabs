from pymongo import MongoClient, ReadPreference 
import redis
import os

# MongoDB connection --> CP configuration
mongo_uri = os.getenv("MONGO_URI", 
                      "mongodb://host.docker.internal:27017,host.docker.internal:27018,host.docker.internal:27019/?replicaSet=rs0"
                      )
client = MongoClient(mongo_uri)
db = client.chat_service


# Configure collection for CP writes + eventual reads
comments_collection = db.get_collection(
    "comments",
    write_concern={"w": "majority"},  # CP: wait for majority before ack --> no duplicates / losses
    read_preference=ReadPreference.SECONDARY_PREFERRED  # Eventual: prefer secondary reads --> high availability
)

# Models collection for caching creator info from events
models_collection = db.get_collection(
    "models",
    write_concern={"w": "majority"}
)


def create_indexes():
    """
    Create database indexes on startup
    """
    
    comments_collection.create_index(
        [("modelId", 1), ("createdAt", -1)],
        background=True
    )
    print("✅ Created index: modelId + createdAt")



# Redis for caching 
redis_host = os.getenv("REDIS_HOST", "host.docker.internal")
redis_port = int(os.getenv("REDIS_PORT", "6379"))
cache = redis.Redis(host=redis_host, port=redis_port, decode_responses=True) # decode_responses --> get strings instead of bytes