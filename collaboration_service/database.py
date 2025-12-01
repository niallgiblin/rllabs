from pymongo import MongoClient, ReadPreference, WriteConcern
import redis
import os

# MongoDB connection --> CP configuration
# Replica set for CP (Consistency + Partition Tolerance)
# Write concern "majority" ensures consistency (waits for majority of nodes)
# Read preference SECONDARY_PREFERRED enables eventual consistency reads (high availability)
mongo_uri = os.getenv("MONGO_URI", 
                      "mongodb://mongo1:27017,mongo2:27018,mongo3:27019/?replicaSet=rs0")
client = MongoClient(mongo_uri)
db = client.chat_service


# Configure collection for CP writes + eventual reads
# Replica set for CP (Consistency + Partition Tolerance)
# Write concern "majority" ensures consistency (waits for majority of nodes)
# Read preference SECONDARY_PREFERRED enables eventual consistency reads (high availability)
write_concern_majority = WriteConcern(w="majority")

comments_collection = db.get_collection(
    "comments",
    write_concern=write_concern_majority,  # CP: wait for majority before ack --> no duplicates / losses
    read_preference=ReadPreference.SECONDARY_PREFERRED  # Eventual: prefer secondary reads --> high availability
)

# Models collection for caching creator info from events
models_collection = db.get_collection(
    "models",
    write_concern=write_concern_majority
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
# Use service name from docker-compose (redis) or fallback to localhost for local dev
redis_host = os.getenv("REDIS_HOST", "redis")
redis_port = int(os.getenv("REDIS_PORT", "6379"))
cache = redis.Redis(host=redis_host, port=redis_port, decode_responses=True) # decode_responses --> get strings instead of bytes