"use strict";

document.addEventListener("DOMContentLoaded", function () {
    // Login page: show or hide the password.
    const passwordInput = document.getElementById("password");
    const passwordToggle = document.getElementById("toggle-password");

    if (passwordInput && passwordToggle) {
        passwordToggle.addEventListener("click", function () {
            const passwordIsHidden = passwordInput.type === "password";

            passwordInput.type = passwordIsHidden ? "text" : "password";
            passwordToggle.textContent = passwordIsHidden ? "Hide" : "Show";
            passwordToggle.setAttribute(
                "aria-label",
                passwordIsHidden ? "Hide password" : "Show password"
            );
            passwordToggle.setAttribute("aria-pressed", String(passwordIsHidden));
        });
    }

    // Claim form: show the selected receipt's name and size.
    const receiptInput = document.getElementById("receipt");
    const receiptFeedback = document.getElementById("receipt-file-feedback");

    if (receiptInput && receiptFeedback) {
        receiptInput.addEventListener("change", function () {
            const file = receiptInput.files[0];

            if (!file) {
                receiptFeedback.textContent = "No receipt selected.";
                receiptFeedback.className = "form-text";
                return;
            }

            const fileSizeMB = file.size / (1024 * 1024);

            if (file.size > 5 * 1024 * 1024) {
                receiptFeedback.textContent =
                    `Selected file: ${file.name} (${fileSizeMB.toFixed(2)} MB). ` +
                    "The maximum file size is 5 MB.";
                receiptFeedback.className = "form-text text-danger";
                return;
            }

            receiptFeedback.textContent =
                `Selected: ${file.name} (${fileSizeMB.toFixed(2)} MB)`;
            receiptFeedback.className = "form-text text-success";
        });
    }

    // Claim lists (My Claims, Claim Review): text search and status filter.
    const claimSearch = document.getElementById("claim-search");
    const claimStatusFilter = document.getElementById("claim-status-filter");
    const claimRows = document.querySelectorAll("#claimRows tr[data-status]");
    const claimFilterEmpty = document.querySelector("[data-filter-empty]");

    function filterClaims() {
        const searchText = claimSearch
            ? claimSearch.value.trim().toLowerCase()
            : "";

        const selectedStatus = claimStatusFilter
            ? claimStatusFilter.value
            : "";

        let visibleCount = 0;

        claimRows.forEach(function (row) {
            const rowText = row.textContent.toLowerCase();
            const matchesSearch = rowText.includes(searchText);
            const matchesStatus =
                !selectedStatus || row.dataset.status === selectedStatus;

            row.hidden = !(matchesSearch && matchesStatus);

            if (!row.hidden) {
                visibleCount += 1;
            }
        });

        if (claimFilterEmpty) {
            claimFilterEmpty.hidden = visibleCount > 0;
        }
    }

    if (claimSearch) {
        claimSearch.addEventListener("input", filterClaims);
    }

    if (claimStatusFilter) {
        claimStatusFilter.addEventListener("change", filterClaims);
    }

    // Profile: enable Save Changes only when a field has changed.
    const profileForm = document.getElementById("profile-form");
    const profileSaveButton = document.getElementById("profile-save-button");

    if (profileForm && profileSaveButton) {
        const trackedInputs = profileForm.querySelectorAll("input[data-original]");

        function updateSaveButton() {
            const hasChanges = Array.from(trackedInputs).some(function (input) {
                return input.value.trim() !== input.dataset.original;
            });

            profileSaveButton.disabled = !hasChanges;
        }

        trackedInputs.forEach(function (input) {
            input.addEventListener("input", updateSaveButton);
        });

        updateSaveButton();
    }

    // Claim form: analyse the receipt with AI and fill in the fields.
    const analyseButton = document.getElementById("analyse-receipt-button");

    if (analyseButton && receiptInput) {
        const claimForm = document.getElementById("claim-form");
        const analysisMessage = document.getElementById("receipt-analysis-message");
        const titleInput = document.getElementById("title");
        const amountInput = document.getElementById("amount");
        const descriptionInput = document.getElementById("description");

        function showAnalysisMessage(text, type) {
            analysisMessage.textContent = text;
            analysisMessage.className = `alert alert-${type} mb-3`;
        }

        analyseButton.addEventListener("click", async function () {
            const receiptFile = receiptInput.files[0];

            if (!receiptFile) {
                showAnalysisMessage("Please choose a receipt file first.", "warning");
                return;
            }

            const formData = new FormData();
            formData.append("receipt", receiptFile);

            const csrfToken = claimForm.querySelector('input[name="csrf_token"]').value;

            analyseButton.disabled = true;
            analyseButton.textContent = "Analysing...";
            analyseButton.classList.add("is-loading");
            showAnalysisMessage("The receipt is being analysed.", "info");

            try {
                const response = await fetch(analyseButton.dataset.analyseUrl, {
                    method: "POST",
                    headers: { "X-CSRFToken": csrfToken },
                    body: formData,
                });

                const contentType = response.headers.get("content-type") || "";

                if (!contentType.includes("application/json")) {
                    throw new Error(
                        "The server returned an unexpected response. Check login and CSRF settings."
                    );
                }

                const data = await response.json();

                if (!response.ok) {
                    throw new Error(data.error || "Receipt analysis failed.");
                }

                titleInput.value = data.title || "";
                amountInput.value = data.amount || "";
                descriptionInput.value = data.description || "";

                showAnalysisMessage(
                    "Receipt analysed. Please check the fields before submitting.",
                    "success"
                );
            } catch (error) {
                showAnalysisMessage(error.message, "danger");
            } finally {
                analyseButton.disabled = false;
                analyseButton.textContent = "Analyse Receipt";
                analyseButton.classList.remove("is-loading");
            }
        });
    }

    // Claim form: lock the buttons after submitting to prevent duplicate claims.
    const claimSubmitForm = document.getElementById("claim-form");

    if (claimSubmitForm) {
        const submitButtons = claimSubmitForm.querySelectorAll('button[type="submit"]');

        claimSubmitForm.addEventListener("submit", function (event) {
            if (claimSubmitForm.dataset.submitting === "true") {
                event.preventDefault();
                return;
            }
            claimSubmitForm.dataset.submitting = "true";

            const clickedButton = event.submitter;

            if (clickedButton && clickedButton.name) {
                const hiddenAction = document.createElement("input");
                hiddenAction.type = "hidden";
                hiddenAction.name = clickedButton.name;
                hiddenAction.value = clickedButton.value;
                hiddenAction.dataset.submitCopy = "true";
                claimSubmitForm.appendChild(hiddenAction);
            }

            submitButtons.forEach(function (button) {
                button.dataset.originalText = button.textContent;
                button.disabled = true;
            });

            if (clickedButton) {
                clickedButton.textContent = "Submitting...";
                clickedButton.classList.add("is-loading");
            }
        });

        window.addEventListener("pageshow", function (event) {
            if (!event.persisted) {
                return;
            }

            delete claimSubmitForm.dataset.submitting;
            claimSubmitForm.querySelectorAll("input[data-submit-copy]").forEach(function (input) {
                input.remove();
            });
            submitButtons.forEach(function (button) {
                button.disabled = false;
                button.classList.remove("is-loading");
                if (button.dataset.originalText) {
                    button.textContent = button.dataset.originalText;
                }
            });
        });
    }

    // Header: show a border once the page is scrolled.
    const siteHeader = document.querySelector("[data-site-header]");

    if (siteHeader) {
        function updateHeaderShadow() {
            siteHeader.classList.toggle("is-scrolled", window.scrollY > 4);
        }

        window.addEventListener("scroll", updateHeaderShadow, { passive: true });
        updateHeaderShadow();
    }

    // Header: mobile menu toggle.
    const navToggle = document.querySelector("[data-nav-toggle]");
    const navMenu = document.querySelector("[data-nav-menu]");

    if (navToggle && navMenu) {
        function setMenuOpen(open) {
            navToggle.setAttribute("aria-expanded", String(open));
            navToggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
            navMenu.classList.toggle("is-open", open);
            document.body.classList.toggle("nav-open", open);
        }

        navToggle.addEventListener("click", function () {
            setMenuOpen(navToggle.getAttribute("aria-expanded") !== "true");
        });

        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && navMenu.classList.contains("is-open")) {
                setMenuOpen(false);
                navToggle.focus();
            }
        });

        window.matchMedia("(min-width: 768px)").addEventListener("change", function (event) {
            if (event.matches) {
                setMenuOpen(false);
            }
        });
    }

    // Toasts: success and info messages close by themselves; errors stay until closed.
    const toasts = document.querySelectorAll("[data-toast]");

    toasts.forEach(function (toast, index) {
        const category = toast.dataset.toastCategory;
        const autoDismiss = !["danger", "warning"].includes(category);
        let timer = null;

        toast.style.animationDelay = `${index * 60}ms`;

        function dismiss() {
            window.clearTimeout(timer);
            toast.classList.add("is-leaving");
            toast.addEventListener("animationend", function () {
                toast.remove();
            }, { once: true });
        }

        function startTimer() {
            if (autoDismiss) {
                timer = window.setTimeout(dismiss, 5000);
            }
        }

        function stopTimer() {
            window.clearTimeout(timer);
        }

        toast.querySelector("[data-toast-close]").addEventListener("click", dismiss);
        toast.addEventListener("mouseenter", stopTimer);
        toast.addEventListener("mouseleave", startTimer);
        toast.addEventListener("focusin", stopTimer);
        toast.addEventListener("focusout", startTimer);

        startTimer();
    });

    // Confirm dialog for forms with data-confirm (falls back to window.confirm).
    const confirmDialog = document.querySelector("[data-confirm-dialog]");
    const confirmForms = document.querySelectorAll("form[data-confirm]");

    if (confirmDialog && typeof confirmDialog.showModal === "function") {
        const confirmMessage = confirmDialog.querySelector("[data-confirm-message]");
        const confirmAccept = confirmDialog.querySelector("[data-confirm-accept]");
        let pendingForm = null;
        let pendingSubmitter = null;

        confirmForms.forEach(function (form) {
            form.addEventListener("submit", function (event) {
                if (form.dataset.confirmed === "true") {
                    delete form.dataset.confirmed;
                    return;
                }

                event.preventDefault();
                pendingForm = form;
                pendingSubmitter = event.submitter || null;

                confirmMessage.textContent = form.dataset.confirm;
                confirmAccept.textContent = form.dataset.confirmAction || "Confirm";
                confirmDialog.returnValue = "";
                confirmDialog.showModal();
            });
        });

        confirmDialog.addEventListener("close", function () {
            if (confirmDialog.returnValue === "confirm" && pendingForm) {
                pendingForm.dataset.confirmed = "true";
                pendingForm.requestSubmit(pendingSubmitter);
            }
            pendingForm = null;
            pendingSubmitter = null;
        });

        confirmDialog.addEventListener("click", function (event) {
            if (event.target === confirmDialog) {
                confirmDialog.close("cancel");
            }
        });
    } else {
        confirmForms.forEach(function (form) {
            form.addEventListener("submit", function (event) {
                if (!window.confirm(form.dataset.confirm)) {
                    event.preventDefault();
                }
            });
        });
    }

    // Profile appearance: apply the chosen theme at once and save it in the background.
    const themeForm = document.querySelector("[data-theme-form]");
    const darkStylesheet = document.getElementById("theme-dark-stylesheet");

    if (themeForm && darkStylesheet) {
        const themeMedia = {
            system: "(prefers-color-scheme: dark)",
            dark: "all",
            light: "not all",
        };
        const themeStatus = themeForm.querySelector("[data-theme-status]");
        const themeSaveButton = themeForm.querySelector("[data-theme-save]");

        let savedTheme = document.documentElement.dataset.theme || "system";
        let statusTimer = null;
        let latestRequest = 0;

        if (themeSaveButton) {
            themeSaveButton.hidden = true;
        }

        function updateThemeColor(theme) {
            document.querySelectorAll('meta[name="theme-color"]').forEach(function (meta) {
                meta.remove();
            });

            const colors = theme === "system"
                ? [["#fafafa", "(prefers-color-scheme: light)"], ["#0b0b0c", "(prefers-color-scheme: dark)"]]
                : [[theme === "dark" ? "#0b0b0c" : "#fafafa", ""]];

            colors.forEach(function ([color, media]) {
                const meta = document.createElement("meta");
                meta.name = "theme-color";
                meta.content = color;
                if (media) {
                    meta.media = media;
                }
                document.head.appendChild(meta);
            });
        }

        function applyTheme(theme) {
            darkStylesheet.media = themeMedia[theme];
            document.documentElement.dataset.theme = theme;
            updateThemeColor(theme);
        }

        function showThemeStatus(message, type) {
            window.clearTimeout(statusTimer);
            themeStatus.textContent = message;
            themeStatus.classList.toggle("is-success", type === "success");
            themeStatus.classList.toggle("is-error", type === "error");

            if (type === "success") {
                statusTimer = window.setTimeout(function () {
                    themeStatus.textContent = "";
                }, 3000);
            }
        }

        themeForm.addEventListener("change", async function (event) {
            const theme = event.target.value;

            if (event.target.name !== "theme" || !(theme in themeMedia)) {
                return;
            }

            applyTheme(theme);
            showThemeStatus("Saving…", "pending");

            const requestId = ++latestRequest;

            try {
                const response = await fetch(themeForm.action, {
                    method: "POST",
                    body: new FormData(themeForm),
                    headers: { Accept: "application/json" },
                });
                const data = await response.json();

                if (!response.ok || !data.ok) {
                    throw new Error(data.error || "Save failed");
                }

                if (requestId === latestRequest) {
                    savedTheme = theme;
                    showThemeStatus("Saved.", "success");
                }
            } catch (error) {
                if (requestId !== latestRequest) {
                    return;
                }

                applyTheme(savedTheme);
                themeForm.querySelector(`input[name="theme"][value="${savedTheme}"]`).checked = true;
                showThemeStatus("Couldn't save. Please try again.", "error");
            }
        });
    }

    // Admin password reset: generate a random 12-character temporary password.
    document.querySelectorAll("[data-generate-password]").forEach(function (button) {
        const passwordInput = document.getElementById(button.dataset.generatePassword);

        if (!passwordInput) {
            return;
        }

        button.addEventListener("click", function () {
            const characters = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789";
            const randomValues = new Uint32Array(12);
            window.crypto.getRandomValues(randomValues);

            passwordInput.value = Array.from(randomValues, function (value) {
                return characters[value % characters.length];
            }).join("");

            passwordInput.focus();
            passwordInput.select();
        });
    });
});
