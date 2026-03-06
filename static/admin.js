const generateBtn = document.getElementById('generate-btn');
const seasonInput = document.getElementById('season-input');
const adminCodeInput = document.getElementById('admin-code-input');
const adminMessage = document.getElementById('admin-message');
const toastInfo = document.getElementById('toast');

function showToast(msg) {
    toastInfo.textContent = msg;
    toastInfo.classList.remove('hidden');
    setTimeout(() => {
        toastInfo.classList.add('hidden');
    }, 3000);
}

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
        generateBtn.textContent = "Generate New Season";
    }
});
