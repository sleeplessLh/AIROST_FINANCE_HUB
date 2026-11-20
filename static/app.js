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
    const claimSearch = document.getElementById("claim-search");
    const claimStatusFilter = document.getElementById("claim-status-filter");
    const claimRows = document.querySelectorAll("#claimRows tr[data-status]");

    function filterClaims() {
        const searchText = claimSearch
            ? claimSearch.value.trim().toLowerCase()
            : "";

        const selectedStatus = claimStatusFilter
            ? claimStatusFilter.value
            : "";

        claimRows.forEach(function (row) {
            const rowText = row.textContent.toLowerCase();
            const matchesSearch = rowText.includes(searchText);
            const matchesStatus =
                !selectedStatus || row.dataset.status === selectedStatus;

            row.hidden = !(matchesSearch && matchesStatus);
        });
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
            }
        });
    }
});