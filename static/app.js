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
});