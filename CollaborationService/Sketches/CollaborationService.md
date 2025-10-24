# Chat/Collaboration Service - Design Summary (Event-Driven Architecture)

## Core Responsibilities
- CRUD operations for comments on maze models
- Threaded conversations (replies to comments)
- Model creator badge distinction
- Event-driven cleanup and synchronization

---

## Technology Stack

| Component | Choice | Why? |
|-----------|--------|------|
| **API** | REST | Simple CRUD, browser-friendly, easy debugging |
| **Database** | MongoDB | - Flexible schema for nested comments <br> - Store models collection (from events) <br> - Primary-Backup replication for CP guarantees <br> - Eventual consistency for reads |
| **Cache** | Redis | Cache comment trees for fast reads, invalidate on writes  |
| **Events** | RabbitMQ/Kafka | - Async notifications <br> - Subscribe to model lifecycle events |


---

## Key API Endpoints

```
POST   /api/models/{modelId}/comments        # Create comment
GET    /api/models/{modelId}/comments        # Get nested comment tree
GET    /api/comments/{commentId}             # Get specific comment
PUT    /api/comments/{commentId}             # Update comment
DELETE /api/comments/{commentId}             # Delete comment
POST   /api/comments/{commentId}/replies     # Reply to comment
```


---

## Critical Design Decisions

### 1. Communication Patterns

**Optimistic Accept + Event-Driven Sync**
- **Fast Write Path**: 
  - Accept comments immediately (no validation blocking)
  - Trust frontend (model page already loaded = model exists)
  - REST with DB for instant CRUD operations
  
- **Asynchronous Synchronization**: 
  - Subscribe to Model Catalog events (ModelCreated, ModelDeleted)
  - Cache model metadata when events arrive
  - Clean up orphaned comments if model deleted
  - Publish CommentCreated events for notifications

**Why This Approach?**
```mermaid
graph LR
    Old[<b>Old: Sync Validation</b><br/>- Slower response<br/>- Coupled to Catalog<br/>- Blocking call]:::red
    
    New[<b>New: Event-Driven</b><br/>- Instant response<br/>- Loosely coupled<br/>- Eventual cleanup]:::green
    
    Old -.Evolved to.-> New
    
    Benefit[<b>Perfect for Research!</b><br/>✅ Fast writes with CP safety<br/>✅ Eventual consistency between services<br/>✅ Faster UX]:::green
    
    New --> Benefit
    
    classDef red fill:#F44336,stroke:#B71C1C,stroke-width:3px,color:#fff
    classDef green fill:#4CAF50,stroke:#2E7D32,stroke-width:3px,color:#fff
```

### 2. CAP Choice: CP System with Eventual Read Consistency

**Context: Research Comments on Maze Designs**

```mermaid
graph TD
    CAP[<b>CAP Theorem</b><br/>Our Choice: CP<br/>with Eventual Reads]:::purple
    
    CAP --> C[<b>Consistency</b>]:::green
    CAP --> A[<b>Availability</b>]:::gray
    CAP --> P[<b>Partition Tolerance</b>]:::green
    
    Choice[<b>Why CP for Comments?</b>]:::orange
    
    Choice --> R1[✅ Research data:<br/>No duplicates/losses]:::green
    Choice --> R2[✅ Low-moderate volume:<br/>Brief downtime acceptable]:::green
    Choice --> R3[✅ High-value data:<br/>Correctness > speed]:::green
    Choice --> R4[✅ Users won't write twice:<br/>30-60s failover OK]:::green
    
    C -.Selected.-> Choice
    P -.Required.-> Choice
    
    Hybrid[<b>Hybrid Approach!</b><br/>CP for writes: no duplicates<br/>Eventual for reads: availabilitys]:::green
    Choice --> Hybrid
    
    classDef purple fill:#9C27B0,stroke:#4A148C,stroke-width:4px,color:#fff
    classDef green fill:#4CAF50,stroke:#2E7D32,stroke-width:3px,color:#fff
    classDef orange fill:#FF9800,stroke:#E65100,stroke-width:3px,color:#fff
    classDef gray fill:#607D8B,stroke:#37474F,stroke-width:2px,color:#fff
```

**Justification:**

**Why CP over AP?**
1. **Research Context**: Comments on maze designs are valuable research data
2. **Low Volume**: Not a high-throughput system requiring extreme availability
3. **Data Integrity**: Duplicate or lost comments are unacceptable for research
4. **Correctness Priority**: Better to wait 30-60 seconds during failover than lose data
5. **User Expectations**: Researchers expect reliability over speed

**The Hybrid Approach:**

```mermaid
graph TB
    subgraph Writes["<b>Write Path: CP Behavior</b>"]
        W[<b>Write Comment</b>]:::orange
        W --> P[<b>PRIMARY</b><br/>Handles all writes]:::blue
        P -->|Replicate| S1[<b>SECONDARY 1</b>]:::gray
        P -->|Replicate| S2[<b>SECONDARY 2</b>]:::gray
        
        P --> WC{<b>Write Concern:<br/>majority</b>}:::purple
        WC -->|✅ Majority OK| Ack[<b>Acknowledge</b><br/>Safe to confirm]:::green
        WC -->|❌ Partition| Reject[<b>Reject Write</b><br/>Can't reach majority]:::red
    end

    classDef blue fill:#2196F3,stroke:#0D47A1,stroke-width:4px,color:#fff
    classDef gray fill:#607D8B,stroke:#37474F,stroke-width:3px,color:#fff
    classDef orange fill:#FF9800,stroke:#E65100,stroke-width:3px,color:#fff
    classDef purple fill:#9C27B0,stroke:#4A148C,stroke-width:3px,color:#fff
    classDef green fill:#4CAF50,stroke:#2E7D32,stroke-width:3px,color:#fff
    classDef red fill:#F44336,stroke:#B71C1C,stroke-width:3px,color:#fff
```

```mermaid
graph TB
    subgraph Reads["<b>Read Path: Eventual Consistency</b>"]
        R[<b>Read Comments</b>]:::orange
        R --> RP{<b>Read Preference:<br/>secondaryPreferred</b>}:::purple
        
        RP -->|Normal| S1R[<b>Read from Secondary</b><br/>⚠️ May be slightly stale<br/>✅ Still available]:::gray
        RP -->|Primary down| S2R[<b>Read from Secondary</b><br/>✅ High availability!]:::green
        RP -->|All secondaries down| PR[<b>Fallback to Primary</b>]:::blue
    end
    
    Benefits[<b>Result:</b><br/>✅ No duplicate/lost comments<br/>✅ High read availability<br/>✅ Perfect for research context]:::green
```


#### Consistency Model: Eventual with Application-Level Ordering

```mermaid
graph LR
    SC[<b>Strong<br/>Consistency</b>]:::red
    EC[<b>Eventual<br/>Consistency</b>]:::orange
    APP[<b>+ Application<br/>Ordering</b>]:::green
    
    SC -->|Too slow for reads| EC
    EC -->|Add parentId logic| APP
    
    APP --> Example[<b>Perfect for Comments</b><br/>Reply always shows<br/>after parent: via parentId<br/>Slight staleness acceptable]:::purple
    
    classDef red fill:#F44336,stroke:#B71C1C,stroke-width:2px,color:#fff
    classDef orange fill:#FF9800,stroke:#E65100,stroke-width:4px,color:#fff
    classDef green fill:#4CAF50,stroke:#2E7D32,stroke-width:3px,color:#fff
    classDef purple fill:#9C27B0,stroke:#4A148C,stroke-width:3px,color:#fff
```


---

### 3. Data Sovereignty
**Own database** (microservices principle)
- Other services access comments ONLY via API
- Never share database
- Learn about models through events, not direct queries

---

## Implementation Phases

```mermaid
graph LR
    P1[<b>Phase 1: MVP</b><br/>✓ REST API<br/>✓ MongoDB with CP config<br/>✓ Fast accept NO validation]:::blue
    
    P2[<b>Phase 2: Events</b><br/>✓ Subscribe to ModelDeleted<br/>✓ Cache model metadata<br/>✓ Publish CommentCreated]:::orange
    
    P3[<b>Phase 3: Optimize</b><br/>✓ Redis caching + invalidation<br/>✓ Pagination<br/>✓ DB indexing]:::green
    
    P1 --> P2 --> P3
    
    classDef blue fill:#2196F3,stroke:#0D47A1,stroke-width:3px,color:#fff
    classDef orange fill:#FF9800,stroke:#E65100,stroke-width:3px,color:#fff
    classDef green fill:#4CAF50,stroke:#2E7D32,stroke-width:3px,color:#fff
```

**Phase Details**:

**Phase 1 - MVP (Simplest)**:
- Accept comments immediately
- MongoDB with CP configuration (writeConcern: majority)
- Trust frontend (if user is on model page → model exists)
- No cross-service validation calls

**Phase 2 - Event-Driven Sync**:
- Subscribe to `ModelDeleted` events → Archive affected comments
- Subscribe to `ModelCreated` events → Cache creator info
- Publish `CommentCreated` events → Trigger notifications

**Phase 3 - Performance**:
1. **Redis caching with invalidation**: 
   - Cache comment trees (5-min TTL)
   - **Invalidate cache on write operations** (POST, PUT, DELETE)
   - Rebuild immediately after invalidation
2. **Pagination**: 50 comments per page
3. **DB indexing**: `(modelId, createdAt DESC)`

---

## Complete Service Architecture

```mermaid
graph TB
    subgraph Clients["<b>Client Layer</b>"]
        Web[<b>Web Browser</b>]:::client
        Mobile[<b>Mobile App</b>]:::client
        Desktop[<b>Desktop App</b>]:::client
    end
    
    subgraph ChatService["<b>Chat/Collaboration Service</b>"]
        API[<b>REST API</b><br/>FastAPI]:::api
        
        Business[<b>Business Logic</b><br/>- Comment CRUD<br/>- Tree building<br/>- Cache invalidation]:::logic
        
        EventHandler[<b>Event Handler</b><br/>- ModelDeleted subscriber<br/>- ModelCreated subscriber]:::event
        
        DB[(<b>MongoDB</b><br/>• Primary-Backup replication<br/>• comments collection<br/>• models collection<br/>• writeConcern: majority<br/>• readPreference: secondaryPreferred)]:::db
        
        Cache[<b>Redis Cache</b><br/>• Nested comment trees<br/>• Invalidate on writes]:::cache
        
        Publisher[<b>Event Publisher</b><br/>CommentCreated events]:::event
    end
    
    subgraph External["<b>External Services</b>"]
        Auth[<b>Auth Service</b><br/>JWT validation]:::external
        EventBus[<b>Event Bus</b><br/>RabbitMQ/Kafka]:::external
        Catalog[<b>Model Catalog</b><br/>Publishes lifecycle events]:::external
    end
    
    Web --> API
    Mobile --> API
    Desktop --> API
    
    API --> Business
    Business --> DB
    Business --> Cache
    
    API -.JWT check.-> Auth
    
    Business -.Publish.-> Publisher
    Publisher --> EventBus
    
    EventBus -.Subscribe.-> EventHandler
    Catalog -.Publish: ModelDeleted<br/>ModelCreated.-> EventBus
    
    EventHandler --> DB
    EventHandler --> Cache
    
    classDef client fill:#4CAF50,stroke:#2E7D32,stroke-width:3px,color:#fff
    classDef api fill:#9C27B0,stroke:#4A148C,stroke-width:4px,color:#fff
    classDef logic fill:#2196F3,stroke:#0D47A1,stroke-width:3px,color:#fff
    classDef db fill:#00BCD4,stroke:#006064,stroke-width:3px,color:#fff
    classDef cache fill:#FF5722,stroke:#BF360C,stroke-width:3px,color:#fff
    classDef event fill:#FFC107,stroke:#F57F17,stroke-width:3px,color:#000
    classDef external fill:#607D8B,stroke:#37474F,stroke-width:2px,color:#fff
```



