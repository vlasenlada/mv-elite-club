(() => {
    "use strict";
    const form = document.querySelector("[data-trial-form]");
    if (!form) return;
    const age = form.elements.namedItem("age_category");
    const guardian = form.querySelector("[data-trial-guardian]");
    const button = form.querySelector("[data-trial-submit]");
    const status = form.querySelector("[data-trial-submit-status]");
    const originalLabel = button.textContent;
    let sending = false;

    function syncGuardian() {
        const minor = age.value === "minor";
        guardian.hidden = !minor;
        guardian.disabled = !minor;
        ["guardian_name", "guardian_consent"].forEach(name => {
            form.elements.namedItem(name).required = minor;
        });
    }
    function resetSending() {
        sending = false;
        button.disabled = false;
        button.textContent = originalLabel;
        form.removeAttribute("aria-busy");
        status.textContent = "";
        syncGuardian();
    }
    age.addEventListener("change", syncGuardian);
    form.addEventListener("submit", event => {
        if (sending) {
            event.preventDefault();
            return;
        }
        // Native validity checks run before submit; the server validates again.
        sending = true;
        button.disabled = true;
        button.textContent = "Надсилаємо заявку…";
        status.textContent = "Зачекайте, будь ласка. Не потрібно натискати повторно.";
        form.setAttribute("aria-busy", "true");
    });
    window.addEventListener("pageshow", resetSending);
    syncGuardian();
})();
