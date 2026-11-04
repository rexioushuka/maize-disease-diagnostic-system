/* Maize Disease Diagnostic System - client helpers
 * - client-side image resizing/compression (NFR-03)
 * - camera capture (FR-07)
 * - drag and drop upload
 * - first-use photo guide (FR-23)
 */
(function () {
    "use strict";

    const MAX_UPLOAD_BYTES = 500 * 1024; // 500 KB
    const MAX_SIDE = 1024;

    function byId(id) {
        return document.getElementById(id);
    }

    function bodyMessage(key, fallback) {
        const value = document.body && document.body.dataset ? document.body.dataset[key] : "";
        return value || fallback;
    }

    function setPreview(file) {
        const preview = byId("image-preview");
        if (!preview || !file) return;
        const reader = new FileReader();
        reader.onload = function (event) {
            preview.src = event.target.result;
            preview.classList.remove("d-none");
        };
        reader.readAsDataURL(file);
    }

    function replaceFileInput(input, file) {
        if (!input) return false;
        try {
            const transfer = new DataTransfer();
            transfer.items.add(file);
            input.files = transfer.files;
            return true;
        } catch (err) {
            return false;
        }
    }

    function loadImage(file) {
        return new Promise(function (resolve, reject) {
            const url = URL.createObjectURL(file);
            const image = new Image();
            image.onload = function () { URL.revokeObjectURL(url); resolve(image); };
            image.onerror = function () { URL.revokeObjectURL(url); reject(new Error("image load failed")); };
            image.src = url;
        });
    }

    function canvasToBlob(canvas, quality) {
        return new Promise(function (resolve) {
            canvas.toBlob(function (blob) { resolve(blob); }, "image/jpeg", quality);
        });
    }

    async function compressImage(file) {
        if (!file || !file.type || file.type.indexOf("image/") !== 0) return file;
        try {
            const image = await loadImage(file);
            let width = image.naturalWidth || image.width;
            let height = image.naturalHeight || image.height;
            const scale = Math.min(1, MAX_SIDE / Math.max(width, height));
            width = Math.max(1, Math.round(width * scale));
            height = Math.max(1, Math.round(height * scale));

            const canvas = document.createElement("canvas");
            canvas.width = width;
            canvas.height = height;
            const ctx = canvas.getContext("2d");
            ctx.drawImage(image, 0, 0, width, height);

            let quality = 0.88;
            let blob = await canvasToBlob(canvas, quality);
            while (blob && blob.size > MAX_UPLOAD_BYTES && quality > 0.35) {
                quality -= 0.1;
                blob = await canvasToBlob(canvas, quality);
            }
            if (!blob) return file;

            const baseName = (file.name || "leaf.jpg").replace(/\.[^.]+$/, "");
            return new File([blob], baseName + ".jpg", { type: "image/jpeg" });
        } catch (err) {
            return file;
        }
    }

    async function handleFile(file, input) {
        if (!file) return;
        if (input) input.dataset.compressing = "1";
        try {
            const compressed = await compressImage(file);
            replaceFileInput(input, compressed);
            setPreview(compressed);
        } finally {
            if (input) delete input.dataset.compressing;
        }
    }

    function setupPhotoGuide() {
        const modalEl = byId("photoGuideModal");
        if (!modalEl || !window.bootstrap) return;

        const storageKey = "maizePhotoGuideSeen";
        let seen = false;
        try { seen = localStorage.getItem(storageKey) === "1"; } catch (err) { seen = false; }

        if (!seen) {
            new bootstrap.Modal(modalEl).show();
        }

        const closeBtn = byId("photo-guide-close");
        const dontShow = byId("dont-show-guide");
        if (closeBtn) {
            closeBtn.addEventListener("click", function () {
                if (dontShow && dontShow.checked) {
                    try { localStorage.setItem(storageKey, "1"); } catch (err) { /* ignore */ }
                }
            });
        }
    }

    function setupUpload() {
        const input = byId("file-input");
        const dropzone = byId("dropzone");
        if (input) {
            input.addEventListener("change", function (event) {
                const file = event.target.files && event.target.files[0];
                if (file) handleFile(file, input);
            });
        }
        if (dropzone && input) {
            dropzone.addEventListener("click", function () { input.click(); });
            ["dragenter", "dragover"].forEach(function (name) {
                dropzone.addEventListener(name, function (event) {
                    event.preventDefault();
                    dropzone.classList.add("dragover");
                });
            });
            ["dragleave", "drop"].forEach(function (name) {
                dropzone.addEventListener(name, function (event) {
                    event.preventDefault();
                    dropzone.classList.remove("dragover");
                });
            });
            dropzone.addEventListener("drop", function (event) {
                if (event.dataTransfer.files && event.dataTransfer.files.length) {
                    handleFile(event.dataTransfer.files[0], input);
                }
            });
        }
    }

    function liveCameraSupported() {
        return !!(navigator.mediaDevices &&
            typeof navigator.mediaDevices.getUserMedia === "function");
    }

    function mobileCameraLikely() {
        return /Android|iPhone|iPad|iPod|Mobile|Tablet/i.test(navigator.userAgent);
    }

    function openNativeCamera(input, onDone) {
        if (!input) return;
        const oldAccept = input.getAttribute("accept");
        const oldCapture = input.getAttribute("capture");
        let finished = false;
        let timer = null;

        function restore() {
            if (finished) return;
            finished = true;
            if (timer) window.clearTimeout(timer);
            if (oldAccept === null) input.removeAttribute("accept");
            else input.setAttribute("accept", oldAccept);
            if (oldCapture === null) input.removeAttribute("capture");
            else input.setAttribute("capture", oldCapture);
            input.removeEventListener("change", onChange);
            window.removeEventListener("focus", onFocus);
            if (typeof onDone === "function") onDone();
        }

        function onChange() {
            restore();
        }

        function onFocus() {
            timer = window.setTimeout(restore, 1200);
        }

        input.setAttribute("accept", "image/*");
        input.setAttribute("capture", "environment");
        input.addEventListener("change", onChange);
        window.addEventListener("focus", onFocus, { once: true });
        timer = window.setTimeout(restore, 120000);
        input.click();
    }

    function setupCamera() {
        const modalEl = byId("cameraModal");
        const video = byId("camera-video");
        const canvas = byId("camera-canvas");
        const captureBtn = byId("camera-capture");
        const errorEl = byId("camera-error");
        const trigger = byId("camera-open");
        const input = byId("file-input");
        if (!modalEl || !video || !canvas || !captureBtn || !input) return;

        let stream = null;
        let nativeFallback = false;

        function showNativeFallback(message) {
            nativeFallback = true;
            video.classList.add("d-none");
            if (!mobileCameraLikely()) captureBtn.classList.add("d-none");
            if (errorEl) {
                errorEl.textContent = message;
                errorEl.classList.remove("d-none");
            }
        }

        if (trigger && !liveCameraSupported() && mobileCameraLikely()) {
            trigger.removeAttribute("data-bs-toggle");
            trigger.removeAttribute("data-bs-target");
            trigger.addEventListener("click", function (event) {
                event.preventDefault();
                openNativeCamera(input);
            });
        }

        async function startCamera() {
            nativeFallback = false;
            video.classList.remove("d-none");
            captureBtn.classList.remove("d-none");
            if (errorEl) errorEl.classList.add("d-none");
            if (!liveCameraSupported()) {
                showNativeFallback(bodyMessage("msgCameraUnsupported",
                    "Live camera requires HTTPS on this network. The camera app will be used instead."));
                return;
            }
            try {
                stream = await navigator.mediaDevices.getUserMedia({
                    video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 } },
                    audio: false
                });
                video.srcObject = stream;
                video.muted = true;
                await video.play();
            } catch (err) {
                showNativeFallback(bodyMessage("msgCameraDenied",
                    "Could not access the camera. Please use the camera app or file picker."));
            }
        }

        function stopCamera() {
            if (stream) {
                stream.getTracks().forEach(function (track) { track.stop(); });
                stream = null;
            }
            video.srcObject = null;
        }

        function hideModal() {
            if (window.bootstrap) {
                const modal = bootstrap.Modal.getInstance(modalEl);
                if (modal) modal.hide();
            }
        }

        modalEl.addEventListener("shown.bs.modal", startCamera);
        modalEl.addEventListener("hidden.bs.modal", stopCamera);

        captureBtn.addEventListener("click", async function () {
            if (nativeFallback || !liveCameraSupported() ||
                    !video.videoWidth || !video.videoHeight) {
                openNativeCamera(input, hideModal);
                return;
            }
            let width = video.videoWidth;
            let height = video.videoHeight;
            const scale = Math.min(1, MAX_SIDE / Math.max(width, height));
            canvas.width = Math.max(1, Math.round(width * scale));
            canvas.height = Math.max(1, Math.round(height * scale));
            canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);

            const blob = await canvasToBlob(canvas, 0.85);
            if (!blob) return;
            const file = new File([blob], "camera-leaf.jpg", { type: "image/jpeg" });
            await handleFile(file, input);
            hideModal();
        });
    }

    function setupFormProgress() {
        const form = byId("diagnosis-form");
        if (!form) return;
        form.addEventListener("submit", function (event) {
            const input = byId("file-input");
            if (input && input.dataset.compressing === "1") {
                event.preventDefault();
                window.alert(bodyMessage("msgPreparing",
                    "Please wait while the image is being prepared."));
                return;
            }
            const button = byId("diagnosis-submit");
            const progress = byId("diagnosis-progress");
            if (button) {
                button.disabled = true;
                button.textContent = button.dataset.loadingText || button.textContent;
            }
            if (progress) progress.classList.remove("d-none");
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        setupPhotoGuide();
        setupUpload();
        setupCamera();
        setupFormProgress();
    });
})();
