// =====================
// Instructions Accordion
// =====================
const instrToggle = document.getElementById('instructions-toggle');
const instrCard = document.querySelector('.instructions-card');

instrToggle.addEventListener('click', () => {
    const isOpen = instrCard.classList.toggle('open');
    instrToggle.setAttribute('aria-expanded', isOpen);
});

// =====================
// Helper: show toast
// =====================
function showToast(type, title, sub = '') {
    const toast = document.getElementById('status-toast');
    const icon = document.getElementById('toast-icon');
    const titleEl = document.getElementById('toast-title');
    const subEl = document.getElementById('toast-sub');

    toast.className = type;
    icon.textContent = type === 'success' ? '✓' : '✕';
    titleEl.textContent = title;
    subEl.textContent = sub;
    toast.classList.remove('hidden');
}

function hideToast() {
    document.getElementById('status-toast').classList.add('hidden');
}

// =====================
// Progress bar helpers
// =====================
const progressBar = document.getElementById('progress-bar');
let progressInterval = null;
let currentFakeProgress = 0;

function startFakeProgress() {
    currentFakeProgress = 5;
    progressBar.style.width = '5%';
    progressInterval = setInterval(() => {
        if (currentFakeProgress < 85) {
            currentFakeProgress += Math.random() * 1.5;
            progressBar.style.width = currentFakeProgress + '%';
        }
    }, 900);
}

function setProgress(pct) {
    clearInterval(progressInterval);
    progressBar.style.width = Math.min(pct, 100) + '%';
}

function stopProgress() {
    clearInterval(progressInterval);
}

// =====================
// Main Form Handler
// =====================
const form = document.getElementById('scraper-form');
const submitBtn = document.getElementById('submit-btn');
const progressPanel = document.getElementById('progress-panel');
const progressLabel = document.getElementById('progress-label');
const statPages = document.getElementById('stat-pages');
const statAgents = document.getElementById('stat-agents');

form.addEventListener('submit', async (e) => {
    e.preventDefault();

    const url = document.getElementById('url').value.trim();

    hideToast();
    progressPanel.classList.remove('hidden');
    submitBtn.disabled = true;
    statPages.textContent = '—';
    statAgents.textContent = '0';
    progressLabel.textContent = 'Connecting to scraper...';
    startFakeProgress();

    let jobId = null;

    // Step 1: Start the job (use relative URL — works on any domain)
    try {
        const res = await fetch('/api/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url })
        });

        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.error || 'Could not start the scraping job.');
        }

        const data = await res.json();
        jobId = data.job_id;
    } catch (err) {
        stopProgress();
        progressPanel.classList.add('hidden');
        submitBtn.disabled = false;
        showToast('error', 'Failed to start scraping', err.message);
        return;
    }

    // Step 2: Stream SSE progress (relative URL)
    const evtSource = new EventSource(`/api/progress/${jobId}`);

    evtSource.onmessage = async (event) => {
        const data = JSON.parse(event.data);

        statAgents.textContent = (data.agents_found || 0).toLocaleString();
        statPages.textContent = data.current_page || '—';
        progressLabel.textContent = `Scraping page ${data.current_page || '…'} — ${(data.agents_found || 0).toLocaleString()} agents found so far...`;

        if (data.status === 'done') {
            evtSource.close();
            setProgress(100);
            progressLabel.textContent = `Complete! ${data.total_agents?.toLocaleString()} agents across ${data.total_pages} pages.`;
            statPages.textContent = data.total_pages;
            statAgents.textContent = data.total_agents?.toLocaleString();

            // Step 3: Trigger download
            try {
                const dlRes = await fetch(`/api/download/${jobId}`);
                if (!dlRes.ok) throw new Error('Download failed.');
                const blob = await dlRes.blob();
                const dlUrl = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = dlUrl;
                a.download = 'compass_agents.xlsx';
                document.body.appendChild(a);
                a.click();
                URL.revokeObjectURL(dlUrl);
                document.body.removeChild(a);

                showToast(
                    'success',
                    'Download complete!',
                    `${data.total_agents?.toLocaleString()} agents from ${data.total_pages} pages saved to Excel.`
                );
                document.getElementById('url').value = '';
            } catch (err) {
                showToast('error', 'Download failed', err.message);
            }

            submitBtn.disabled = false;
        }

        if (data.status === 'error') {
            evtSource.close();
            stopProgress();
            setProgress(0);
            progressPanel.classList.add('hidden');
            submitBtn.disabled = false;
            showToast('error', 'Scraping failed', data.error || 'An unexpected error occurred.');
        }
    };

    evtSource.onerror = () => {
        evtSource.close();
        stopProgress();
        progressPanel.classList.add('hidden');
        submitBtn.disabled = false;
        showToast('error', 'Connection lost', 'Could not reach the server. Please try again.');
    };
});
