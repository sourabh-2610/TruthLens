   (() => {
      const elements = {
        cameraPanel: document.getElementById("cameraPanel"),
        cameraStatus: document.getElementById("cameraStatus"),
        cameraStream: document.getElementById("cameraStream"),
        captureButton: document.getElementById("captureBtn"),
        charCount: document.getElementById("charCount"),
        feedbackMount: document.getElementById("feedbackMount"),
        fileInput: document.getElementById("fileInput"),
        fileName: document.getElementById("fileName"),
        form: document.getElementById("mainForm"),
        historyPanel: document.querySelector(".history-panel"),
        imagePreview: document.getElementById("imgPreview"),
        imageViewer: document.getElementById("imageViewer"),
        imageViewerImg: document.getElementById("imageViewerImg"),
        mobileMenuButton: document.getElementById("mobileMenuBtn"),
        previewImage: document.getElementById("previewImg"),
        recentsList: document.getElementById("recentsList"),
        resultMount: document.getElementById("resultMount"),
        serverPreviewMount: document.getElementById("serverPreviewMount"),
        sidebarBackdrop: document.getElementById("sidebarBackdrop"),
        stopButton: document.getElementById("stopCameraBtn"),
        submitButton: document.getElementById("submitBtn"),
        uploadZone: document.getElementById("uploadZone"),
        authModal: document.getElementById("authModal"),
        authMessage: document.getElementById("authMessage"),
        authTitle: document.getElementById("authTitle"),
        loginForm: document.getElementById("loginForm"),
        signupForm: document.getElementById("signupForm"),
        loginTab: document.getElementById("loginTab"),
        signupTab: document.getElementById("signupTab"),
        authForms: Array.from(document.querySelectorAll("[data-auth-form]")),
        authTabs: Array.from(document.querySelectorAll("[data-auth-tab]")),
        authMessages: Array.from(document.querySelectorAll("[data-auth-message]")),
        guestForms: Array.from(document.querySelectorAll("[data-guest-form]")),
      };

      const configElement = document.getElementById("truthlensConfig");
      const IS_AUTHENTICATED = configElement?.dataset.isAuthenticated === "true";
      const HISTORY_ENABLED = configElement?.dataset.historyEnabled === "true";
      let SERVER_RECENTS = [];

      try {
        const savedRecents = JSON.parse(configElement?.dataset.serverRecents || "[]");
        SERVER_RECENTS = Array.isArray(savedRecents) ? savedRecents : [];
      } catch (error) {
        console.warn("Could not read saved analyses.", error);
      }
      let cameraStream = null;
      let activeRecentId = null;
      let swipeStartX = 0;
      let swipeStartY = 0;
      let swipeTracking = false;
      let didHydrateServerRecents = false;
      const ANALYSIS_TIMEOUT_MS = 90000;
      const IMAGE_RESIZE_TIMEOUT_MS = 6000;
      const SERVER_WAKE_NOTICE_MS = 12000;
      const HISTORY_KEY = "tl-recents";
      const MAX_RECENTS = 6;
      const SWIPE_EDGE_WIDTH = 90;
      const SWIPE_DISTANCE = 42;

      function applySavedTheme() {
        const savedTheme = localStorage.getItem("tl-theme");
        if (savedTheme) {
          document.documentElement.setAttribute("data-theme", savedTheme);
        }
      }

      function toggleTheme() {
        const html = document.documentElement;
        const nextTheme = html.getAttribute("data-theme") === "dark" ? "light" : "dark";
        html.setAttribute("data-theme", nextTheme);
        localStorage.setItem("tl-theme", nextTheme);
      }

      function updateCount(textarea) {
        const length = textarea.value.length;
        elements.charCount.textContent = length > 0 ? `${length} character${length !== 1 ? "s" : ""}` : "";
        elements.charCount.style.color = length > 2000 ? "var(--red)" : "";
      }

      function removeServerPreview() {
        document.querySelectorAll(".server-preview").forEach((preview) => preview.remove());
      }

      function previewSelectedFile(file) {
        elements.fileName.textContent = file.name || "camera-capture.png";
        elements.uploadZone.style.borderColor = "var(--accent)";
        removeServerPreview();

        const reader = new FileReader();
        reader.onload = (event) => {
          elements.previewImage.src = event.target.result;
          elements.imagePreview.classList.add("show");
        };
        reader.readAsDataURL(file);
      }

      function clearImagePreview() {
        elements.fileName.textContent = "";
        elements.previewImage.src = "";
        elements.imagePreview.classList.remove("show");
        elements.uploadZone.style.borderColor = "";
      }

      function showFileName(input) {
        input.setAttribute("name", "image");

        if (input.files && input.files[0]) {
          previewSelectedFile(input.files[0]);
        } else {
          clearImagePreview();
        }
      }

      function setCameraStatus(message, tone) {
        elements.cameraStatus.textContent = message || "";
        elements.cameraStatus.style.color =
          tone === "error" ? "var(--red)" : tone === "success" ? "var(--accent)" : "var(--muted)";
      }

      function setCameraButtons(isActive) {
        elements.captureButton.disabled = !isActive;
        elements.stopButton.disabled = !isActive;
      }

      function openSidebar() {
        elements.historyPanel.classList.add("open");
        elements.sidebarBackdrop.classList.add("show");
        elements.mobileMenuButton.classList.add("is-hidden");
        elements.mobileMenuButton.setAttribute("aria-label", "Close menu");
      }

      function closeSidebar() {
        elements.historyPanel.classList.remove("open");
        elements.sidebarBackdrop.classList.remove("show");
        elements.mobileMenuButton.classList.remove("is-hidden");
        elements.mobileMenuButton.setAttribute("aria-label", "Open menu");
      }

      function toggleSidebar() {
        if (elements.historyPanel.classList.contains("open")) {
          closeSidebar();
        } else {
          openSidebar();
        }
      }

      function rememberSwipeStart(x, y) {
        swipeStartX = x;
        swipeStartY = y;
        swipeTracking = true;
      }

      function getSwipeState(x, y) {
        const deltaX = x - swipeStartX;
        const deltaY = y - swipeStartY;
        const mostlyHorizontal = Math.abs(deltaX) > Math.abs(deltaY) * 1.4;
        const sidebarOpen = elements.historyPanel.classList.contains("open");

        return { deltaX, mostlyHorizontal, sidebarOpen };
      }

      function openSidebarFromSwipe(x, y) {
        if (!swipeTracking) {
          return;
        }

        const { deltaX, mostlyHorizontal, sidebarOpen } = getSwipeState(x, y);

        if (sidebarOpen || !mostlyHorizontal) {
          return;
        }

        if (swipeStartX <= SWIPE_EDGE_WIDTH && deltaX > SWIPE_DISTANCE) {
          openSidebar();
          swipeTracking = false;
        }
      }

      function finishSwipe(x, y) {
        if (!swipeTracking) {
          return;
        }

        const { deltaX, mostlyHorizontal, sidebarOpen } = getSwipeState(x, y);

        if (!mostlyHorizontal) {
          swipeTracking = false;
          return;
        }

        if (!sidebarOpen && swipeStartX <= SWIPE_EDGE_WIDTH && deltaX > SWIPE_DISTANCE) {
          openSidebar();
          swipeTracking = false;
          return;
        }

        if (sidebarOpen && deltaX < -SWIPE_DISTANCE) {
          closeSidebar();
        }

        swipeTracking = false;
      }

      function handleTouchStart(event) {
        const touch = event.touches && event.touches[0];
        if (!touch) {
          return;
        }

        rememberSwipeStart(touch.clientX, touch.clientY);
      }

      function handleTouchEnd(event) {
        const touch = event.changedTouches && event.changedTouches[0];
        if (!touch) {
          return;
        }

        finishSwipe(touch.clientX, touch.clientY);
      }

      function handleTouchMove(event) {
        const touch = event.touches && event.touches[0];
        if (!touch) {
          return;
        }

        openSidebarFromSwipe(touch.clientX, touch.clientY);
      }

      function handlePointerDown(event) {
        if (event.pointerType === "mouse" && event.button !== 0) {
          return;
        }

        rememberSwipeStart(event.clientX, event.clientY);
      }

      function handlePointerUp(event) {
        if (event.pointerType === "mouse" && event.button !== 0) {
          return;
        }

        finishSwipe(event.clientX, event.clientY);
      }

      function handlePointerMove(event) {
        openSidebarFromSwipe(event.clientX, event.clientY);
      }

      function openImageViewer(src) {
        if (!src) {
          return;
        }

        elements.imageViewerImg.src = src;
        elements.imageViewer.classList.add("show");
      }

      function closeImageViewer() {
        elements.imageViewer.classList.remove("show");
        elements.imageViewerImg.src = "";
      }

      function switchAuthMode(mode) {
        if (elements.authForms.length === 0) {
          return;
        }

        const isSignup = mode === "signup";
        if (elements.authTitle) {
          elements.authTitle.textContent = isSignup ? "Create Account" : "Login";
        }
        elements.authForms.forEach((form) => {
          form.classList.toggle("active", form.dataset.authMode === mode);
        });
        elements.authTabs.forEach((tab) => {
          tab.classList.toggle("active", tab.dataset.authTab === mode);
        });
        elements.authMessages.forEach((message) => {
          message.textContent = "";
        });
      }

      function openAuthModal(mode = "login") {
        if (!elements.authModal) {
          return;
        }

        switchAuthMode(mode);
        elements.authModal.classList.add("show");
      }

      function closeAuthModal() {
        if (!elements.authModal) {
          return;
        }

        elements.authModal.classList.remove("show");
      }

      function continueAsGuest() {
        fetch("/guest", {
          method: "POST",
          headers: {
            Accept: "application/json",
            "X-Requested-With": "XMLHttpRequest",
          },
        })
          .then(() => {
            window.location.href = "/";
          })
          .catch(() => {
            window.location.href = "/";
          });
      }

      async function handleAuthSubmit(event) {
        event.preventDefault();

        const form = event.currentTarget;
        const button = form.querySelector("button[type='submit']");
        const message = form.parentElement.querySelector(".auth-message") || elements.authMessage;
        button.disabled = true;
        if (message) {
          message.textContent = "";
        }

        try {
          const response = await fetch(form.action, {
            method: "POST",
            body: new FormData(form),
            headers: {
              Accept: "application/json",
              "X-Requested-With": "XMLHttpRequest",
            },
          });
          const data = await response.json();

          if (!response.ok || !data.ok) {
            throw new Error(data.error || "Account request failed.");
          }

          window.location.reload();
        } catch (error) {
          if (message) {
            message.textContent = error.message || "Please try again.";
          }
        } finally {
          button.disabled = false;
        }
      }

      function escapeHtml(value) {
        const node = document.createElement("div");
        node.textContent = value == null ? "" : String(value);
        return node.innerHTML;
      }

      function renderFeedback(message) {
        elements.feedbackMount.innerHTML = message
          ? `<div class="error-box">Warning: ${escapeHtml(message)}</div>`
          : "";
      }

      function renderServerPreview(data) {
        elements.serverPreviewMount.innerHTML = "";

        if (!data.uploaded_image_url || elements.previewImage.src) {
          return;
        }

        elements.serverPreviewMount.innerHTML = `
          <div class="img-preview show server-preview">
            <img src="${escapeHtml(data.uploaded_image_url)}" alt="Uploaded image" onclick="openImageViewer(this.src)" />
          </div>
        `;
      }

      function renderResult(data) {
        if (!data.show_result) {
          elements.resultMount.innerHTML = "";
          return;
        }

        const resultClass = data.result_class === "real" ? "real" : "fake";
        const confidence = data.confidence == null ? "N/A" : `${escapeHtml(data.confidence)}%`;
        const confidenceLabel = escapeHtml(data.ui?.confidence_score || "Confidence Score");
        const extractedLabel = escapeHtml(data.ui?.extracted_text_label || "Extracted Text");
        const imageTypeLabel = escapeHtml(data.ui?.image_type_label || "Input Source");
        const reasonLabel = escapeHtml(data.ui?.reason_label || "Reason");
        const imageType = data.image_type
          ? `
            <div class="image-type-panel">
              <span>${imageTypeLabel}</span>
              <strong>${escapeHtml(data.image_type)}</strong>
            </div>
          `
          : "";
        const extractedText = data.extracted_text
          ? `
            <div class="extracted-section">
              <p class="extracted-label">${extractedLabel}</p>
              <p class="extracted-text">${escapeHtml(data.extracted_text)}</p>
            </div>
          `
          : "";
        const humanExplanation = data.human_explanation
          ? `
            <div class="ai-explain-panel">
              <span>AI Explanation</span>
              <p>${escapeHtml(data.human_explanation)}</p>
            </div>
          `
          : "";
        const reportAction = data.report_url
          ? `<a class="report-btn" href="${escapeHtml(data.report_url)}">Download Report</a>`
          : "";

        elements.resultMount.innerHTML = `
          <section class="result-wrap" aria-label="Analysis result">
            <div class="result-card ${resultClass}">
              <div class="result-header">
                <div class="result-icon">${resultClass === "real" ? "&check;" : "&times;"}</div>
                <div>
                  <div class="result-verdict">${escapeHtml(data.display_prediction || data.prediction)}</div>
                  <div class="result-desc">
                    <span class="reason-label">${reasonLabel}</span>
                    ${escapeHtml(data.reason)}
                  </div>
                </div>
              </div>

              <div class="conf-row">
                <span class="conf-label">${confidenceLabel}</span>
                <span class="conf-value" id="confVal">${confidence}</span>
              </div>

              <div class="conf-track">
                <div class="conf-fill" id="confFill"></div>
              </div>

              ${imageType}
              ${extractedText}
              ${humanExplanation}
              ${reportAction ? `<div class="result-actions">${reportAction}</div>` : ""}
            </div>
          </section>
        `;

        animateConfidence();
      }

      function renderAnalysisResponse(data) {
        renderFeedback(data.error);
        renderServerPreview(data);
        renderResult(data);

        const focusTarget = data.show_result ? elements.resultMount : elements.feedbackMount;
        if (focusTarget.textContent.trim()) {
          focusTarget.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
      }

      function getRecents() {
        if (!HISTORY_ENABLED) {
          return [];
        }

        try {
          let recents = JSON.parse(localStorage.getItem(HISTORY_KEY)) || [];

          if (IS_AUTHENTICATED && !didHydrateServerRecents) {
            didHydrateServerRecents = true;
            const localOnly = recents.filter((item) => !item.serverId);
            recents = [...SERVER_RECENTS, ...localOnly].slice(0, MAX_RECENTS);
            setRecents(recents);
          }

          return recents;
        } catch (error) {
          return IS_AUTHENTICATED ? SERVER_RECENTS : [];
        }
      }

      function setRecents(items) {
        if (!HISTORY_ENABLED) {
          return;
        }

        localStorage.setItem(HISTORY_KEY, JSON.stringify(items.slice(0, MAX_RECENTS)));
      }

      function buildRecentTitle(data) {
        const typedText = document.getElementById("newsText").value.trim();
        const sourceText = typedText || data.extracted_text || elements.fileName.textContent || "Untitled analysis";
        const compact = sourceText.replace(/\s+/g, " ").trim();
        return compact.length > 54 ? `${compact.slice(0, 54)}...` : compact;
      }

      function renderRecents() {
        if (!elements.recentsList || !HISTORY_ENABLED) {
          return;
        }

        const recents = getRecents();
        elements.recentsList.innerHTML = "";

        if (recents.length === 0) {
          const empty = document.createElement("button");
          empty.type = "button";
          empty.className = "recent-item empty";
          empty.textContent = "No recent analyses";
          empty.disabled = true;
          elements.recentsList.appendChild(empty);
          return;
        }

        recents.forEach((item) => {
          const row = document.createElement("div");
          row.className = item.id === activeRecentId ? "recent-row active" : "recent-row";

          const button = document.createElement("button");
          button.type = "button";
          button.className = "recent-item";
          button.textContent = item.title;
          button.title = item.title;
          button.addEventListener("click", () => loadRecentAnalysis(item.id));

          const deleteButton = document.createElement("button");
          deleteButton.type = "button";
          deleteButton.className = "delete-recent-btn";
          deleteButton.textContent = "\u00d7";
          deleteButton.title = "Delete recent";
          deleteButton.setAttribute("aria-label", `Delete ${item.title}`);
          deleteButton.addEventListener("click", () => deleteRecentAnalysis(item.id));

          row.appendChild(button);
          row.appendChild(deleteButton);
          elements.recentsList.appendChild(row);
        });
      }

      function saveRecentAnalysis(data) {
        if (!HISTORY_ENABLED || !data.show_result) {
          return;
        }

        const id = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
        const item = {
          id,
          serverId: data.analysis_id || null,
          title: buildRecentTitle(data),
          newsText: document.getElementById("newsText").value.trim(),
          data,
        };
        activeRecentId = id;
        const recents = getRecents().filter((recent) => {
          if (item.serverId && recent.serverId === item.serverId) {
            return false;
          }

          return recent.title !== item.title;
        });
        setRecents([item, ...recents]);
        renderRecents();
      }

      function loadRecentAnalysis(id) {
        const item = getRecents().find((recent) => recent.id === id);
        if (!item) {
          return;
        }

        if (!elements.form) {
          window.location.href = "/";
          return;
        }

        stopCamera();
        activeRecentId = id;
        elements.form.reset();
        document.getElementById("newsText").value = item.newsText || "";
        updateCount(document.getElementById("newsText"));
        clearImagePreview();
        renderAnalysisResponse(item.data);
        renderRecents();
        closeSidebar();
      }

      async function deleteRecentAnalysis(id) {
        const target = getRecents().find((recent) => recent.id === id);
        if (target && target.serverId) {
          try {
            await fetch(`/history/${target.serverId}/delete`, {
              method: "POST",
              headers: {
                Accept: "application/json",
                "X-Requested-With": "XMLHttpRequest",
              },
            });
          } catch (error) {
            // Keep local delete responsive even if the network is temporarily unavailable.
          }
        }

        const recents = getRecents().filter((recent) => recent.id !== id);
        setRecents(recents);

        if (activeRecentId === id) {
          clearAll();
          return;
        }

        renderRecents();
      }

      function setLoadingState(isLoading) {
        elements.submitButton.classList.toggle("loading", isLoading);
        elements.submitButton.disabled = isLoading;
      }

      function resizeImageFile(file, maxSize = 1000, quality = 0.68) {
        if (!file || !file.type.startsWith("image/")) {
          return Promise.resolve(file);
        }

        return new Promise((resolve) => {
          const image = new Image();
          const objectUrl = URL.createObjectURL(file);
          let isSettled = false;

          const finish = (result) => {
            if (isSettled) {
              return;
            }

            isSettled = true;
            window.clearTimeout(timeoutId);
            URL.revokeObjectURL(objectUrl);
            resolve(result);
          };

          const timeoutId = window.setTimeout(() => finish(file), IMAGE_RESIZE_TIMEOUT_MS);

          image.onload = () => {
            const largestSide = Math.max(image.naturalWidth, image.naturalHeight);
            if (largestSide <= maxSize) {
              finish(file);
              return;
            }

            const scale = maxSize / largestSide;
            const canvas = document.createElement("canvas");
            canvas.width = Math.round(image.naturalWidth * scale);
            canvas.height = Math.round(image.naturalHeight * scale);
            const context = canvas.getContext("2d");

            if (!context) {
              finish(file);
              return;
            }

            context.drawImage(image, 0, 0, canvas.width, canvas.height);

            canvas.toBlob(
              (blob) => {
                if (!blob) {
                  finish(file);
                  return;
                }

                const name = file.name.replace(/\.[^.]+$/, "") || "news-image";
                finish(new File([blob], `${name}-optimized.jpg`, { type: "image/jpeg" }));
              },
              "image/jpeg",
              quality,
            );
          };

          image.onerror = () => {
            finish(file);
          };

          image.src = objectUrl;
        });
      }

      async function buildAnalysisFormData() {
        const formData = new FormData(elements.form);
        const imageFile = elements.fileInput.files && elements.fileInput.files[0];

        if (imageFile) {
          try {
            setCameraStatus("Preparing image for analysis...", "success");
            const optimizedImage = await resizeImageFile(imageFile);
            formData.set("image", optimizedImage, optimizedImage.name);
          } finally {
            setCameraStatus("");
          }
        }

        return formData;
      }

      async function handleSubmit(event) {
        event.preventDefault();
        setLoadingState(true);
        renderFeedback("");
        elements.resultMount.innerHTML = "";

        const hasImage = Boolean(elements.fileInput.files && elements.fileInput.files[0]);
        const controller = new AbortController();
        let timeoutId = null;
        let wakeNoticeId = null;

        try {
          const formData = await buildAnalysisFormData();
          timeoutId = window.setTimeout(() => controller.abort(), ANALYSIS_TIMEOUT_MS);

          if (hasImage) {
            setCameraStatus("Reading the image and analyzing the news...", "success");
            wakeNoticeId = window.setTimeout(() => {
              setCameraStatus("The server is waking up. The first analysis may take a little longer.", "success");
            }, SERVER_WAKE_NOTICE_MS);
          }

          const response = await fetch(elements.form.action, {
            method: "POST",
            body: formData,
            signal: controller.signal,
            headers: {
              Accept: "application/json",
              "X-Requested-With": "XMLHttpRequest",
            },
          });

          const responseText = await response.text();
          let data;
          try {
            data = JSON.parse(responseText);
          } catch (error) {
            throw new Error("The server restarted or timed out during OCR. Please wait a few seconds and try again.");
          }

          if (!response.ok) {
            throw new Error(data.error || `Server error ${response.status}`);
          }

          renderAnalysisResponse(data);
          saveRecentAnalysis(data);

          if (window.location.pathname !== "/") {
            window.history.replaceState({}, "", "/");
          }
        } catch (error) {
          const message =
            error.name === "AbortError"
              ? "The server did not respond in time. Please try once more; the next request is usually faster."
              : error.message || "Something went wrong while analyzing. Please try again.";
          renderFeedback(message);
        } finally {
          if (timeoutId) {
            window.clearTimeout(timeoutId);
          }
          if (wakeNoticeId) {
            window.clearTimeout(wakeNoticeId);
          }
          if (hasImage) {
            setCameraStatus("");
          }
          setLoadingState(false);
        }
      }

      function cameraErrorMessage(error) {
        const name = error && error.name;

        if (name === "NotAllowedError" || name === "SecurityError") {
          return "Camera permission is blocked. Allow camera in the browser address bar, or upload an image.";
        }

        if (name === "NotFoundError" || name === "DevicesNotFoundError") {
          return "No camera was found. Upload an image instead.";
        }

        if (name === "NotReadableError" || name === "TrackStartError") {
          return "Camera is being used by another app. Close it, then try Open Camera again.";
        }

        return "Camera is unavailable. Upload an image instead.";
      }

      async function startCamera() {
        const mediaDevices = window.navigator && window.navigator.mediaDevices;

        if (!mediaDevices || !mediaDevices.getUserMedia) {
          setCameraStatus("Camera is not supported in this browser. Upload an image instead.", "error");
          return;
        }

        stopCamera(false);

        const attempts = [
          { video: { facingMode: { ideal: "environment" } }, audio: false },
          { video: true, audio: false },
        ];

        let lastError = null;

        try {
          for (const constraints of attempts) {
            try {
              cameraStream = await mediaDevices.getUserMedia(constraints);
              break;
            } catch (error) {
              lastError = error;
            }
          }

          if (!cameraStream) {
            throw lastError;
          }

          elements.cameraStream.srcObject = cameraStream;
          await elements.cameraStream.play();
          elements.cameraPanel.classList.add("show");
          setCameraButtons(true);
          setCameraStatus("Camera ready.", "success");
        } catch (error) {
          cameraStream = null;
          setCameraButtons(false);
          setCameraStatus(cameraErrorMessage(error), "error");
        }
      }

      function stopCamera(clearStatus = true) {
        if (!elements.cameraStream) {
          return;
        }

        if (cameraStream) {
          cameraStream.getTracks().forEach((track) => track.stop());
          cameraStream = null;
        }

        elements.cameraStream.srcObject = null;
        elements.cameraPanel.classList.remove("show");
        setCameraButtons(false);

        if (clearStatus) {
          setCameraStatus("");
        }
      }

      function capturePhoto() {
        const canvas = document.getElementById("cameraCanvas");

        if (!cameraStream) {
          setCameraStatus("Open the camera first.", "error");
          return;
        }

        if (elements.cameraStream.readyState < 2) {
          setCameraStatus("Camera is still loading.", "error");
          return;
        }

        canvas.width = elements.cameraStream.videoWidth || 1280;
        canvas.height = elements.cameraStream.videoHeight || 720;
        canvas.getContext("2d").drawImage(elements.cameraStream, 0, 0, canvas.width, canvas.height);

        canvas.toBlob(
          (blob) => {
            if (!blob) {
              setCameraStatus("Could not capture the image.", "error");
              return;
            }

            const file = new File([blob], "camera-capture.png", { type: "image/png" });
            const files = new DataTransfer();
            files.items.add(file);
            elements.fileInput.files = files.files;
            elements.fileInput.setAttribute("name", "image");
            showFileName(elements.fileInput);
            stopCamera(false);
            setCameraStatus("Captured photo ready for analysis.", "success");
          },
          "image/png",
          0.95,
        );
      }

      function clearAll() {
        if (!elements.form) {
          window.location.href = "/";
          return;
        }

        activeRecentId = null;
        elements.form.reset();
        stopCamera();
        elements.charCount.textContent = "";
        elements.fileInput.setAttribute("name", "image");
        clearImagePreview();
        elements.feedbackMount.innerHTML = "";
        elements.resultMount.innerHTML = "";
        elements.serverPreviewMount.innerHTML = "";

        if (window.location.pathname !== "/") {
          window.history.replaceState({}, "", "/");
        }

        renderRecents();
      }

      function startNewChat() {
        if (!elements.form) {
          window.location.href = "/";
          return;
        }

        clearAll();
        closeSidebar();
        elements.form.scrollIntoView({ behavior: "smooth", block: "start" });
      }

      function setExample(chip) {
        const textarea = document.getElementById("newsText");
        textarea.value = chip.textContent.trim();
        updateCount(textarea);
        textarea.focus();
      }

      function animateConfidence() {
        const fill = document.getElementById("confFill");
        const value = document.getElementById("confVal");

        if (!fill || !value) {
          return;
        }

        const percent = parseFloat(value.textContent) || 0;
        setTimeout(() => {
          fill.style.width = `${percent}%`;
        }, 350);
      }

      window.toggleTheme = toggleTheme;
      window.updateCount = updateCount;
      window.showFileName = showFileName;
      window.startCamera = startCamera;
      window.stopCamera = stopCamera;
      window.capturePhoto = capturePhoto;
      window.clearAll = clearAll;
      window.openSidebar = openSidebar;
      window.closeSidebar = closeSidebar;
      window.toggleSidebar = toggleSidebar;
      window.openImageViewer = openImageViewer;
      window.closeImageViewer = closeImageViewer;
      window.openAuthModal = openAuthModal;
      window.closeAuthModal = closeAuthModal;
      window.switchAuthMode = switchAuthMode;
      window.continueAsGuest = continueAsGuest;
      window.startNewChat = startNewChat;
      window.setExample = setExample;

      applySavedTheme();
      renderRecents();
      if (elements.form) {
        elements.form.addEventListener("submit", handleSubmit);
      }
      if (elements.previewImage) {
        elements.previewImage.addEventListener("click", () => openImageViewer(elements.previewImage.src));
      }
      if (elements.loginForm) {
        elements.loginForm.addEventListener("submit", handleAuthSubmit);
      }
      if (elements.signupForm) {
        elements.signupForm.addEventListener("submit", handleAuthSubmit);
      }
      window.addEventListener("pointerdown", handlePointerDown, { passive: true });
      window.addEventListener("pointermove", handlePointerMove, { passive: true });
      window.addEventListener("pointerup", handlePointerUp, { passive: true });
      window.addEventListener("touchstart", handleTouchStart, { passive: true });
      window.addEventListener("touchmove", handleTouchMove, { passive: true });
      window.addEventListener("touchend", handleTouchEnd, { passive: true });
      window.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          closeImageViewer();
          closeSidebar();
        }
      });
      window.addEventListener("load", animateConfidence);
      window.addEventListener("beforeunload", () => stopCamera(false));
    })();
