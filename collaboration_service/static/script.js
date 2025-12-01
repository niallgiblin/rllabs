const API_BASE = 'http://localhost:8080/api';

// State
let currentModelId = document.getElementById('simModelId').value;
let currentUserId = document.getElementById('simUserId').value;
let currentUserName = document.getElementById('simUserName').value;

// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
    fetchComments();
});

function reloadContext() {
    currentModelId = document.getElementById('simModelId').value;
    currentUserId = document.getElementById('simUserId').value;
    currentUserName = document.getElementById('simUserName').value;
    fetchComments();
}

// --- API Calls ---

async function fetchComments() {
    const list = document.getElementById('commentsList');
    list.innerHTML = '<div class="loading">Loading discussions...</div>';

    try {
        const res = await fetch(`${API_BASE}/models/${currentModelId}/comments`);
        const data = await res.json();
        
        renderTree(data.data);
        document.getElementById('commentCount').innerText = `${data.pagination.total} comments`;
    } catch (err) {
        list.innerHTML = `<div class="error">Error loading comments: ${err.message}</div>`;
    }
}

async function postComment(parentId = null, content = null) {
    const isRoot = parentId === null;
    
    // Get content from main input or the specific reply input
    let finalContent = content;
    if (isRoot) {
        const input = document.getElementById('mainCommentInput');
        finalContent = input.value;
        if (!finalContent.trim()) return;
        input.value = ''; // Clear input
    }

    const payload = {
        content: finalContent,
        authorId: currentUserId,
        authorName: currentUserName,
        parentId: parentId
    };

    try {
        const res = await fetch(`${API_BASE}/models/${currentModelId}/comments`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (res.ok) {
            fetchComments(); // Refresh tree
        }
    } catch (err) {
        alert("Failed to post comment");
    }
}

async function deleteComment(commentId) {
    if(!confirm("Delete this comment and all replies?")) return;

    try {
        await fetch(`${API_BASE}/comments/${commentId}`, { method: 'DELETE' });
        fetchComments();
    } catch (err) {
        alert("Failed to delete");
    }
}



// --- Rendering Logic (Recursive) ---

function renderTree(comments) {
    const list = document.getElementById('commentsList');
    list.innerHTML = '';

    if (comments.length === 0) {
        list.innerHTML = '<div style="color:#94a3b8; text-align:center; padding:20px;">No comments yet. Be the first!</div>';
        return;
    }

    comments.forEach(comment => {
        list.appendChild(createCommentNode(comment));
    });
}



function createCommentNode(comment) {
    const node = document.createElement('div');
    node.className = 'comment-node';

    // 1. Calculate Badge HTML
    const badgeHtml = comment.isCreator 
        ? `<span class="creator-badge" title="Model Creator">★ Creator</span>` 
        : '';

    // 2. Format Date
    const date = new Date(comment.createdAt).toLocaleString(undefined, { 
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' 
    });

    // 3. Create Avatar Initials
    const initials = comment.authorName.substring(0,2).toUpperCase();

    // 4. Construct HTML
    node.innerHTML = `
        <div class="comment-card">
            <div class="comment-header">
                <div class="author-info">
                    <div class="author-avatar">${initials}</div>
                    <span class="author-name">${comment.authorName}</span>
                    ${badgeHtml}
                </div>
                <span class="timestamp">${date}</span>
            </div>
            <div class="comment-content">${escapeHtml(comment.content)}</div>
            <div class="comment-actions">
                <button class="btn-text" onclick="toggleReplyForm('${comment.id}')">Reply</button>
                ${comment.authorId === currentUserId ? `<button class="btn-text delete" onclick="deleteComment('${comment.id}')">Delete</button>` : ''}
            </div>
            
            <div id="reply-form-${comment.id}" class="reply-box">
                <textarea id="reply-input-${comment.id}" placeholder="Write a reply..."></textarea>
                <button class="btn-primary" onclick="submitReply('${comment.id}')">Post Reply</button>
                <button class="btn-secondary" onclick="toggleReplyForm('${comment.id}')">Cancel</button>
            </div>
        </div>
        <div class="replies-container" id="replies-${comment.id}"></div>
    `;

    // 5. Recursively render replies
    const repliesContainer = node.querySelector(`#replies-${comment.id}`);
    if (comment.replies && comment.replies.length > 0) {
        comment.replies.forEach(reply => {
            repliesContainer.appendChild(createCommentNode(reply));
        });
    }

    return node;
}

// --- UI Helpers ---

function toggleReplyForm(id) {
    const form = document.getElementById(`reply-form-${id}`);
    form.classList.toggle('active');
}

function submitReply(parentId) {
    const input = document.getElementById(`reply-input-${parentId}`);
    const content = input.value;
    if (!content.trim()) return;
    
    postComment(parentId, content);
}

function escapeHtml(text) {
    // Basic XSS prevention
    if (!text) return "";
    return text.replace(/&/g, "&amp;")
               .replace(/</g, "&lt;")
               .replace(/>/g, "&gt;")
               .replace(/"/g, "&quot;")
               .replace(/'/g, "&#039;");
}