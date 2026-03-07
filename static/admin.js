{
    const generateBtn = document.getElementById('generate-btn');
    const resetDraftBtn = document.getElementById('reset-draft-btn');
    const scrapePredictionsBtn = document.getElementById('scrape-predictions-btn');
    const seasonInput = document.getElementById('season-input');
    const adminCodeInput = document.getElementById('admin-code-input');
    const adminMessage = document.getElementById('admin-message');
    const toastInfo = document.getElementById('toast');

    function showToast(msg) {
        if (!toastInfo) return;
        toastInfo.textContent = msg;
        toastInfo.classList.remove('hidden');
        setTimeout(() => {
            toastInfo.classList.add('hidden');
        }, 3000);
    }

    if (generateBtn) {
        generateBtn.addEventListener('click', async () => {
            const season = seasonInput.value.trim();
            const adminCode = adminCodeInput.value.trim();

            if (!season || !adminCode) {
                showToast("Please enter both Season and Admin Code.");
                return;
            }

            generateBtn.disabled = true;
            generateBtn.textContent = "Generating...";
            adminMessage.textContent = "";
            adminMessage.style.color = "inherit";

            try {
                const response = await fetch('/api/admin/new_season', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        season: season,
                        admin_code: adminCode
                    })
                });

                const data = await response.json();

                if (!response.ok) {
                    adminMessage.style.color = "var(--accent-red)";
                    adminMessage.textContent = data.error || "An error occurred.";
                } else {
                    adminMessage.style.color = "var(--accent-green)";
                    adminMessage.textContent = data.message;
                    seasonInput.value = '';
                    adminCodeInput.value = '';
                }
            } catch (err) {
                adminMessage.style.color = "var(--accent-red)";
                adminMessage.textContent = "Network error. Please try again.";
                console.error(err);
            } finally {
                generateBtn.disabled = false;
                generateBtn.textContent = "Generate Season";
            }
        });
    }

    if (resetDraftBtn) {
        resetDraftBtn.addEventListener('click', async () => {
            const season = seasonInput.value.trim();
            const adminCode = adminCodeInput.value.trim();

            if (!season || !adminCode) {
                showToast("Please enter both Season and Admin Code.");
                return;
            }

            if (!confirm(`Are you absolutely sure you want to PERMANENTLY delete all draft results for ${season}? This will wipe the full board.`)) {
                return;
            }

            resetDraftBtn.disabled = true;
            resetDraftBtn.textContent = "Wiping Data...";
            adminMessage.textContent = "";

            try {
                const response = await fetch('/api/admin/reset_draft', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        season: season,
                        admin_code: adminCode
                    })
                });

                const data = await response.json();

                if (!response.ok) {
                    adminMessage.style.color = "var(--accent-red)";
                    adminMessage.textContent = data.error || "An error occurred.";
                } else {
                    adminMessage.style.color = "var(--accent-green)";
                    adminMessage.textContent = data.message;
                }
            } catch (err) {
                adminMessage.style.color = "var(--accent-red)";
                adminMessage.textContent = "Network error. Please try again.";
            } finally {
                resetDraftBtn.disabled = false;
                resetDraftBtn.textContent = "Reset Draft Results";
            }
        });
    }

    if (scrapePredictionsBtn) {
        scrapePredictionsBtn.addEventListener('click', async () => {
            const adminCode = adminCodeInput.value.trim();

            if (!adminCode) {
                showToast("Please enter Admin Code to execute the Agent.");
                return;
            }

            scrapePredictionsBtn.disabled = true;
            scrapePredictionsBtn.textContent = "Agent is scraping the web...";
            adminMessage.textContent = "";

            try {
                const response = await fetch('/api/admin/scrape_predictions', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        admin_code: adminCode
                    })
                });

                const data = await response.json();

                if (!response.ok) {
                    adminMessage.style.color = "var(--accent-red)";
                    adminMessage.textContent = data.error || "An error occurred executing the script.";
                } else {
                    adminMessage.style.color = "var(--accent-green)";
                    adminMessage.textContent = data.message;
                }
            } catch (err) {
                adminMessage.style.color = "var(--accent-red)";
                adminMessage.textContent = "Network error while calling Predictor Agent.";
            } finally {
                scrapePredictionsBtn.disabled = false;
                scrapePredictionsBtn.textContent = "Run Preseason Predictor Agent";
            }
        });
    }
}
