"use strict";

document.addEventListener("DOMContentLoaded", function () {
    // 登录页：切换密码的显示状态
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

    // Claim 表单：显示用户选中的收据文件名和大小
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

    // Claim 列表：按文字搜索和状态筛选
    // My Claims（Member）和 Claim Review（Treasurer）共用这一段
    const claimSearch = document.getElementById("claim-search");
    const claimStatusFilter = document.getElementById("claim-status-filter");
    const claimRows = document.querySelectorAll("#claimRows tr[data-status]");
    // 全部被筛掉时显示的提示
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

    // 个人资料页：只有资料真的有变动时，才能按 Save Changes
    const profileForm = document.getElementById("profile-form");
    const profileSaveButton = document.getElementById("profile-save-button");

    if (profileForm && profileSaveButton) {
        // 只追踪有 data-original 属性的输入框（电话 + 三个银行栏位）
        const trackedInputs = profileForm.querySelectorAll("input[data-original]");

        function updateSaveButton() {
            // some()：只要有任何一个输入框和原始值不同，就返回 true
            // trim()：和后端一样去掉前后空格，只多打空格不算修改
            const hasChanges = Array.from(trackedInputs).some(function (input) {
                return input.value.trim() !== input.dataset.original;
            });

            profileSaveButton.disabled = !hasChanges;
        }

        // 每次输入（打字、删除、贴上）都重新检查
        trackedInputs.forEach(function (input) {
            input.addEventListener("input", updateSaveButton);
        });

        // 页面载入时先检查一次，决定按钮的初始状态
        updateSaveButton();
    }

    // Claim 表单：用 AI 分析收据并自动填写（Submit Claim 与 Edit Claim 共用）
    const analyseButton = document.getElementById("analyse-receipt-button");

    if (analyseButton && receiptInput) {
        const claimForm = document.getElementById("claim-form");
        const analysisMessage = document.getElementById("receipt-analysis-message");
        const titleInput = document.getElementById("title");
        const amountInput = document.getElementById("amount");
        const descriptionInput = document.getElementById("description");

        // 显示分析结果信息；type 对应 Bootstrap alert 颜色
        function showAnalysisMessage(text, type) {
            analysisMessage.textContent = text;
            analysisMessage.className = `alert alert-${type} mb-3`;
        }

        analyseButton.addEventListener("click", async function () {
            // 必须先选择收据文件
            const receiptFile = receiptInput.files[0];

            if (!receiptFile) {
                showAnalysisMessage("Please choose a receipt file first.", "warning");
                return;
            }

            // 建立 multipart/form-data 请求内容
            const formData = new FormData();
            formData.append("receipt", receiptFile);

            // 从表单隐藏字段读取 CSRF Token
            const csrfToken = claimForm.querySelector('input[name="csrf_token"]').value;

            // 请求进行中：禁用按钮，避免重复点击
            analyseButton.disabled = true;
            analyseButton.textContent = "Analysing...";
            analyseButton.classList.add("is-loading");
            showAnalysisMessage("The receipt is being analysed.", "info");

            try {
                // data-analyse-url：由 Template 用 url_for() 产生，JS 文件里不能直接写 Jinja
                const response = await fetch(analyseButton.dataset.analyseUrl, {
                    method: "POST",
                    headers: { "X-CSRFToken": csrfToken },
                    body: formData,
                });

                // 先确认服务器返回的是 JSON
                const contentType = response.headers.get("content-type") || "";

                if (!contentType.includes("application/json")) {
                    throw new Error(
                        "The server returned an unexpected response. Check login and CSRF settings."
                    );
                }

                const data = await response.json();

                // 非 2xx 状态码表示请求失败
                if (!response.ok) {
                    throw new Error(data.error || "Receipt analysis failed.");
                }

                // 将 AI 识别结果填入表单
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
                // 成功或失败后都恢复按钮
                analyseButton.disabled = false;
                analyseButton.textContent = "Analyse Receipt";
                analyseButton.classList.remove("is-loading");
            }
        });
    }


    // Claim 表单：送出后锁定按钮，防止重复提交（Submit Claim 与 Edit Claim 共用）
    const claimSubmitForm = document.getElementById("claim-form");

    if (claimSubmitForm) {
        const submitButtons = claimSubmitForm.querySelectorAll('button[type="submit"]');

        claimSubmitForm.addEventListener("submit", function (event) {
            // 已经送出过一次：直接阻止，不再送出
            if (claimSubmitForm.dataset.submitting === "true") {
                event.preventDefault();
                return;
            }
            claimSubmitForm.dataset.submitting = "true";

            // event.submitter：用户按下的那个按钮（Save as Draft 或 Submit）
            const clickedButton = event.submitter;

            // disabled 的按钮不会送出 name/value，所以先复制成隐藏栏位，后端才知道是 draft 还是 submit
            if (clickedButton && clickedButton.name) {
                const hiddenAction = document.createElement("input");
                hiddenAction.type = "hidden";
                hiddenAction.name = clickedButton.name;
                hiddenAction.value = clickedButton.value;
                hiddenAction.dataset.submitCopy = "true";
                claimSubmitForm.appendChild(hiddenAction);
            }

            // 锁定所有送出按钮，并提示正在处理
            submitButtons.forEach(function (button) {
                button.dataset.originalText = button.textContent;
                button.disabled = true;
            });

            if (clickedButton) {
                clickedButton.textContent = "Submitting...";
                clickedButton.classList.add("is-loading");
            }
        });

        // 用浏览器"返回"回到这一页时，页面可能直接从快取还原，按钮仍是锁定状态
        // pageshow + persisted：代表页面来自快取，把表单恢复成可以再送出的状态
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

    // ============================================================
    // 导航栏：捲动后出现细边框
    // ============================================================
    const siteHeader = document.querySelector("[data-site-header]");

    if (siteHeader) {
        function updateHeaderShadow() {
            siteHeader.classList.toggle("is-scrolled", window.scrollY > 4);
        }

        // passive：告诉浏览器这个监听不会阻止捲动，捲动更顺
        window.addEventListener("scroll", updateHeaderShadow, { passive: true });
        updateHeaderShadow();
    }

    // ============================================================
    // 导航栏：手机版汉堡选单
    // ============================================================
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

        // 按 Esc 关闭选单，并把焦点还给按钮
        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && navMenu.classList.contains("is-open")) {
                setMenuOpen(false);
                navToggle.focus();
            }
        });

        // 视窗变宽到桌面版时，自动收起手机选单
        window.matchMedia("(min-width: 768px)").addEventListener("change", function (event) {
            if (event.matches) {
                setMenuOpen(false);
            }
        });
    }

    // ============================================================
    // Toast：Flash 信息右上角提示
    // 成功 / 资讯 5 秒后自动消失（滑鼠移上去或键盘 focus 时暂停）
    // 错误 / 警告不会自动消失，要按 × 关闭
    // ============================================================
    const toasts = document.querySelectorAll("[data-toast]");

    toasts.forEach(function (toast, index) {
        const category = toast.dataset.toastCategory;
        const autoDismiss = !["danger", "warning"].includes(category);
        let timer = null;

        // 依序出现：每则延迟 60ms
        toast.style.animationDelay = `${index * 60}ms`;

        function dismiss() {
            window.clearTimeout(timer);
            toast.classList.add("is-leaving");
            // 等离开动画结束再移除；如果系统关闭了动画，animationend 仍会立刻触发
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

    // ============================================================
    // 确认视窗：表单有 data-confirm 时，送出前先确认
    // 使用原生 <dialog>：自带焦点锁定、Esc 关闭、背景遮罩
    // ============================================================
    const confirmDialog = document.querySelector("[data-confirm-dialog]");
    const confirmForms = document.querySelectorAll("form[data-confirm]");

    if (confirmDialog && typeof confirmDialog.showModal === "function") {
        const confirmMessage = confirmDialog.querySelector("[data-confirm-message]");
        const confirmAccept = confirmDialog.querySelector("[data-confirm-accept]");
        let pendingForm = null;
        let pendingSubmitter = null;

        confirmForms.forEach(function (form) {
            form.addEventListener("submit", function (event) {
                // 已经在视窗按过确认：放行，真正送出
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

        // 视窗关闭时（按按钮或 Esc），看用户选了什么
        confirmDialog.addEventListener("close", function () {
            if (confirmDialog.returnValue === "confirm" && pendingForm) {
                pendingForm.dataset.confirmed = "true";
                // requestSubmit：和用户按下按钮一样，会再跑一次表单验证与 submit 事件
                pendingForm.requestSubmit(pendingSubmitter);
            }
            pendingForm = null;
            pendingSubmitter = null;
        });

        // 点视窗外的遮罩也算取消
        confirmDialog.addEventListener("click", function (event) {
            if (event.target === confirmDialog) {
                confirmDialog.close("cancel");
            }
        });
    } else {
        // 很旧的浏览器没有 <dialog>：退回原生 confirm()
        confirmForms.forEach(function (form) {
            form.addEventListener("submit", function (event) {
                if (!window.confirm(form.dataset.confirm)) {
                    event.preventDefault();
                }
            });
        });
    }

    // ============================================================
    // 外观设定（Profile 页面）：跟随系统 / 亮色 / 暗色
    // 选择后马上套用，并在背景储存，不用重新载入页面
    // 暗色 Token 在 theme-dark.css，只要改那个 <link> 的 media 就能切换
    // ============================================================
    const themeForm = document.querySelector("[data-theme-form]");
    const darkStylesheet = document.getElementById("theme-dark-stylesheet");

    if (themeForm && darkStylesheet) {
        // 每个选项对应的 media：和 layout.html 的 theme_media 一样
        const themeMedia = {
            system: "(prefers-color-scheme: dark)",
            dark: "all",
            light: "not all",
        };
        const themeStatus = themeForm.querySelector("[data-theme-status]");
        const themeSaveButton = themeForm.querySelector("[data-theme-save]");

        // 目前已经存进数据库的外观；储存失败时用来复原
        let savedTheme = document.documentElement.dataset.theme || "system";
        let statusTimer = null;
        let latestRequest = 0;

        // 有 JavaScript：选择后会自动储存，所以不需要储存按钮
        if (themeSaveButton) {
            themeSaveButton.hidden = true;
        }

        // 手机浏览器顶部的颜色也一起换
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

        // type：pending（储存中）/ success（成功，3 秒后消失）/ error（失败，保留）
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

            // 先套用，让用户马上看到效果
            applyTheme(theme);
            showThemeStatus("Saving…", "pending");

            // 连续快速点选时，只处理最后一次的结果
            const requestId = ++latestRequest;

            try {
                // FormData 会带上 csrf_token 和选中的 theme
                // Accept: application/json：告诉后端回传 JSON，而不是跳转页面
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

                // 储存失败（例如登入过期、网络中断）：恢复成原本储存的外观
                applyTheme(savedTheme);
                themeForm.querySelector(`input[name="theme"][value="${savedTheme}"]`).checked = true;
                showThemeStatus("Couldn't save. Please try again.", "error");
            }
        });
    }

    // ============================================================
    // Admin 重设密码：按 Generate 自动产生 12 位临时密码
    // crypto.getRandomValues：浏览器内建的安全随机数（比 Math.random 安全）
    // 去掉容易看错的字元（0/O、1/l/I），方便当面交给用户
    // ============================================================
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

            // 选取起来，方便 Admin 复制
            passwordInput.focus();
            passwordInput.select();
        });
    });
});
