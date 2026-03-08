{
    const generateBtn = document.getElementById('generate-btn');
    const deleteSeasonBtn = document.getElementById('delete-season-btn');
    const resetDraftBtn = document.getElementById('reset-draft-btn');
    const createPlayerBtn = document.getElementById('create-player-btn');
    const scrapePredictionsBtn = document.getElementById('scrape-predictions-btn');

    const seasonInput = document.getElementById('season-input');
    const adminCodeInput = document.getElementById('admin-code-input');
    const playerGrid = document.getElementById('player-grid');
    const adminMessage = document.getElementById('admin-message');
    const toastInfo = document.getElementById('toast');

    // Player creation inputs
    const newPlayerName = document.getElementById('new-player-name');
    const newPlayerNick = document.getElementById('new-player-nick');
    const newPlayerEmail = document.getElementById('new-player-email');

    let allPlayers = [];

    function showToast(msg) {
        if (!toastInfo) return;
        toastInfo.textContent = msg;
        toastInfo.classList.remove('hidden');
        setTimeout(() => {
            toastInfo.classList.add('hidden');
        }, 3000);
    }

    function setMessage(msg, color = 'inherit') {
        adminMessage.textContent = msg;
        adminMessage.style.color = color;
    }

    async function fetchPlayers() {
        const adminCode = adminCodeInput.value.trim();
        if (!adminCode) return;

        try {
            const resp = await fetch(`/api/admin/players?admin_code=${adminCode}`);
            if (resp.ok) {
                allPlayers = await resp.json();
                renderPlayerGrid();
            }
        } catch (err) {
            console.error("Failed to fetch players:", err);
        }
    }

    function renderPlayerGrid() {
        if (!playerGrid) return;
        playerGrid.innerHTML = '';
        allPlayers.forEach(p => {
            const label = document.createElement('label');
            label.style.display = 'flex';
            label.style.alignItems = 'center';
            label.style.gap = '0.5rem';
            label.style.cursor = 'pointer';
            label.style.padding = '0.4rem';
            label.style.background = 'rgba(255,255,255,0.05)';
            label.style.borderRadius = '4px';

            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.value = p.playerId;
            checkbox.className = 'player-checkbox';

            // Auto-check some by default if needed, but usually manual is better

            label.appendChild(checkbox);
            label.appendChild(document.createTextNode(p.nickName || p.fullName));
            playerGrid.appendChild(label);
        });
    }

    // Refresh players when admin code changes (simple trigger)
    adminCodeInput.addEventListener('blur', fetchPlayers);

    if (generateBtn) {
        generateBtn.addEventListener('click', async () => {
            const season = seasonInput.value.trim();
            const adminCode = adminCodeInput.value.trim();
            const selectedPids = Array.from(document.querySelectorAll('.player-checkbox:checked')).map(cb => cb.value);

            if (!season || !adminCode) {
                showToast("Please enter both Season and Admin Code.");
                return;
            }

            if (selectedPids.length !== 10) {
                showToast(`Please select exactly 10 players (currently ${selectedPids.length}).`);
                return;
            }

            generateBtn.disabled = true;
            generateBtn.textContent = "Generating...";
            setMessage("");

            try {
                const response = await fetch('/api/admin/new_season', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        season: season,
                        admin_code: adminCode,
                        playerIds: selectedPids
                    })
                });

                const data = await response.json();
                if (!response.ok) {
                    setMessage(data.error || "An error occurred.", "var(--accent-red)");
                } else {
                    setMessage(data.message, "var(--accent-green)");
                }
            } catch (err) {
                setMessage("Network error. Please try again.", "var(--accent-red)");
            } finally {
                generateBtn.disabled = false;
                generateBtn.textContent = "Generate Season";
            }
        });
    }

    if (deleteSeasonBtn) {
        deleteSeasonBtn.addEventListener('click', async () => {
            const season = seasonInput.value.trim();
            const adminCode = adminCodeInput.value.trim();

            if (!season || !adminCode) {
                showToast("Enter Season and Admin Code to wipe.");
                return;
            }

            if (!confirm(`CRITICAL: This will PERMANENTLY delete the draft order, rules, and all pick results for ${season}. Continue?`)) {
                return;
            }

            deleteSeasonBtn.disabled = true;
            deleteSeasonBtn.textContent = "Wiping...";

            try {
                const response = await fetch('/api/admin/delete_season', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ season, admin_code: adminCode })
                });
                const data = await response.json();
                if (response.ok) {
                    setMessage(data.message, "var(--accent-green)");
                } else {
                    setMessage(data.error, "var(--accent-red)");
                }
            } catch (err) {
                setMessage("Error wiping season.", "var(--accent-red)");
            } finally {
                deleteSeasonBtn.disabled = false;
                deleteSeasonBtn.textContent = "Wipe Season Data";
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

            if (!confirm(`Wipe all draft results (picks) for ${season}? The order and rules will remain.`)) {
                return;
            }

            resetDraftBtn.disabled = true;
            resetDraftBtn.textContent = "Wiping Picks...";

            try {
                const response = await fetch('/api/admin/reset_draft', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ season, admin_code: adminCode })
                });

                const data = await response.json();
                if (response.ok) {
                    setMessage(data.message, "var(--accent-green)");
                } else {
                    setMessage(data.error || "Error resetting picks.", "var(--accent-red)");
                }
            } catch (err) {
                setMessage("Network error.", "var(--accent-red)");
            } finally {
                resetDraftBtn.disabled = false;
                resetDraftBtn.textContent = "Reset Draft Picks (Current Season Only)";
            }
        });
    }

    if (createPlayerBtn) {
        createPlayerBtn.addEventListener('click', async () => {
            const adminCode = adminCodeInput.value.trim();
            const fullName = newPlayerName.value.trim();
            const nickName = newPlayerNick.value.trim();
            const email = newPlayerEmail.value.trim();

            if (!adminCode || !fullName || !nickName || !email) {
                showToast("All player fields and Admin Code are required.");
                return;
            }

            createPlayerBtn.disabled = true;
            createPlayerBtn.textContent = "Creating...";

            try {
                const response = await fetch('/api/admin/create_player', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ admin_code: adminCode, fullName, nickName, email })
                });
                const data = await response.json();
                if (response.ok) {
                    showToast("Player created successfully!");
                    newPlayerName.value = '';
                    newPlayerNick.value = '';
                    newPlayerEmail.value = '';
                    fetchPlayers(); // Refresh grid
                } else {
                    setMessage(data.error, "var(--accent-red)");
                }
            } catch (err) {
                setMessage("Error creating player.", "var(--accent-red)");
            } finally {
                createPlayerBtn.disabled = false;
                createPlayerBtn.textContent = "Add New Player Entrant";
            }
        });
    }

    if (scrapePredictionsBtn) {
        scrapePredictionsBtn.addEventListener('click', async () => {
            const adminCode = adminCodeInput.value.trim();
            if (!adminCode) {
                showToast("Please enter Admin Code.");
                return;
            }

            scrapePredictionsBtn.disabled = true;
            scrapePredictionsBtn.textContent = "Agent is scraping...";

            try {
                const response = await fetch('/api/admin/scrape_predictions', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ admin_code: adminCode })
                });
                const data = await response.json();
                if (response.ok) {
                    setMessage(data.message, "var(--accent-green)");
                } else {
                    setMessage(data.error, "var(--accent-red)");
                }
            } catch (err) {
                setMessage("Error calling Agent.", "var(--accent-red)");
            } finally {
                scrapePredictionsBtn.disabled = false;
                scrapePredictionsBtn.textContent = "Run Preseason Predictor Agent";
            }
        });
    }
}
