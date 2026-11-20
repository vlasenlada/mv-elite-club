(() => {
    "use strict";
    const prefix = "mv-knowledge:";
    const completed = document.querySelector("[data-kc-completed]");
    if (completed) {
        try {
            const start = prefix + completed.dataset.kcCompleted + ":";
            Object.keys(sessionStorage).filter(key => key.startsWith(start)).forEach(key => sessionStorage.removeItem(key));
        } catch (_) { /* Storage is optional; server-side results remain available. */ }
    }
    const form = document.querySelector("[data-kc-draft]");
    if (!form) return;
    const key = prefix + form.dataset.kcDraft;
    const fields = Array.from(form.querySelectorAll('[name="answer"]'));
    const status = form.querySelector(".kc-draft-status");
    try {
        const stored = JSON.parse(sessionStorage.getItem(key) || "null");
        if (form.dataset.kcRestore === "yes" && Array.isArray(stored)) {
            fields.forEach(field => {
                if (field.type === "radio" || field.type === "checkbox") field.checked = stored.includes(field.value);
                else field.value = typeof stored[0] === "string" ? stored[0].slice(0, 26) : "";
            });
        }
        const save = () => {
            try {
                sessionStorage.setItem(key, JSON.stringify(fields.filter(field => !["radio", "checkbox"].includes(field.type) || field.checked).map(field => field.value)));
                status.textContent = "Чернетку збережено в цій вкладці. Натисни кнопку переходу, щоб зберегти відповідь на сервері.";
            } catch (_) {
                status.textContent = "Чернетка браузера недоступна. Зберігай відповідь кнопкою переходу.";
            }
        };
        form.addEventListener("input", save);
        form.addEventListener("change", save);
    } catch (_) {
        status.textContent = "Чернетка браузера недоступна. Зберігай відповідь кнопкою переходу.";
    }
})();
