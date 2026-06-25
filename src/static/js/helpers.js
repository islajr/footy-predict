/**
 * Footy Predict - Shared Client-Side Javascript Helpers
 */

/**
 * Toast Notification System
 */
function showToast(message, type = 'success', duration = 4000) {
    const container = document.getElementById('toast-container');
    if (!container) {
        // Fallback to console if container doesn't exist yet
        console.log(`[Toast ${type}]: ${message}`);
        return;
    }

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    
    let icon = '⚽';
    if (type === 'success') icon = '✓';
    else if (type === 'warning') icon = '⚠️';
    else if (type === 'error') icon = '🛑';
    else if (type === 'info') icon = 'ℹ️';

    const iconSpan = document.createElement('span');
    iconSpan.className = 'toast-icon';
    iconSpan.textContent = icon;

    const messageSpan = document.createElement('span');
    messageSpan.className = 'toast-message';
    messageSpan.textContent = message;

    const dismissBtn = document.createElement('button');
    dismissBtn.className = 'toast-dismiss';
    dismissBtn.textContent = '×';
    dismissBtn.onclick = () => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(10px)';
        toast.style.transition = 'all 0.22s ease-out';
        setTimeout(() => toast.remove(), 220);
    };

    toast.appendChild(iconSpan);
    toast.appendChild(messageSpan);
    toast.appendChild(dismissBtn);

    container.appendChild(toast);
    
    // Trigger animation frame for transition
    requestAnimationFrame(() => {
        toast.classList.add('active');
    });

    // Auto-remove after duration
    setTimeout(() => {
        if (toast.parentElement) {
            toast.style.opacity = '0';
            toast.style.transform = 'translateY(10px)';
            toast.style.transition = 'all 0.3s ease-out';
            setTimeout(() => toast.remove(), 300);
        }
    }, duration);
}

// Override native alert to show Toast
window.alert = function(message) {
    let type = 'info';
    const msg = String(message).toLowerCase();
    if (msg.includes('error') || msg.includes('failed') || msg.includes('fail')) {
        type = 'error';
    } else if (msg.includes('success') || msg.includes('saved') || msg.includes('complete') || msg.includes('deleted')) {
        type = 'success';
    }
    showToast(message, type);
};

/**
 * Test remote API keys and update UI status indicator
 * @param {string} provider - 'api_football' or 'football_data_org'
 */
function testKey(provider) {
    const keyInput = document.getElementById(provider + '_key');
    const statusDiv = document.getElementById('status-' + provider);
    if (!keyInput || !statusDiv) return;

    const key = keyInput.value.trim();
    statusDiv.innerText = 'Testing connection...';
    statusDiv.className = 'test-status';
    statusDiv.style.color = 'var(--text-secondary)';

    fetch('/api/v1/settings/test-key', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: provider, key: key })
    })
    .then(r => {
        if (!r.ok) throw new Error("API request failed");
        return r.json();
    })
    .then(data => {
        if (data.valid) {
            statusDiv.innerText = '✓ Key valid. Quota: ' + data.quota_remaining + ' calls remaining.';
            statusDiv.style.color = 'var(--color-green)';
        } else {
            statusDiv.innerText = '✗ Invalid: ' + (data.error || 'Check credentials');
            statusDiv.style.color = 'var(--color-red)';
        }
    })
    .catch(e => {
        statusDiv.innerText = '✗ Test failed: ' + e.message;
        statusDiv.style.color = 'var(--color-red)';
    });
}
