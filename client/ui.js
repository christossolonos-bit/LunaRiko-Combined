import { VRM_PATH, WS_URL, HTTP_URL }       from './config.js';
import { startVideoStream, stopVideoStream, isVideoStreaming } from './interaction/videoStream.js';

let ws;

let currentImageFile = null;
let isTranscriptionMode = false;

// Connect WebSocket
export function connectWS(onMessage) {
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    console.log("✅ Connected to WebSocket");
  };

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    onMessage(msg); // Pass message to VRM app.js
    
    // Handle transcription results
    if (msg.type === "transcription_result") {
      handleTranscriptionResult(msg.text);
    }
  };

  ws.onclose = () => {
    console.warn("❌ WebSocket disconnected. Reconnecting in 2s...");
    setTimeout(() => connectWS(onMessage), 2000);
  };
}

// Handle transcription result
function handleTranscriptionResult(transcribedText) {
  const textInput = document.getElementById("text-input");
  textInput.value = transcribedText;
  textInput.placeholder = "Edit your transcription and press Enter to send...";
  isTranscriptionMode = true;
  
  // Auto-resize textarea and check layout
  autoResizeAndLayout(textInput);
  
  // Focus and select all text for easy editing
  textInput.focus();
  textInput.select();
  
  // Update send button state immediately after transcription
  updateSendButton();
}

// =========== caches & tuning ===========
let _singleLineHeightCached = null;
const MAX_TEXTAREA_HEIGHT = 120;
const ENTER_THRESHOLD = 10; // Much more aggressive threshold
const EXIT_THRESHOLD = 18;
const EXTRA_BUFFER = 3;
const _textMeasureCanvas = document.createElement('canvas');
const _textMeasureCtx = _textMeasureCanvas.getContext('2d');

// =========== helpers ===========
function measureSingleLineHeight(textarea) {
  if (_singleLineHeightCached) return _singleLineHeightCached;

  const cs = window.getComputedStyle(textarea);

  // Parse font-size (px)
  const fontSizePx = parseFloat(cs.fontSize) || 16;

  // Resolving line-height:
  // - if it's a px value, use it
  // - if it's numeric/unitless like "1.2", multiply by fontSize
  // - if it's "normal" or unparsable, fall back to 1.2 multiplier
  let lineHeightVal = cs.lineHeight;
  let lineHeightPx = null;

  if (!lineHeightVal || lineHeightVal === 'normal') {
    lineHeightPx = fontSizePx * 1.2;
  } else if (lineHeightVal.endsWith('px')) {
    lineHeightPx = parseFloat(lineHeightVal);
  } else {
    const numeric = parseFloat(lineHeightVal);
    if (!isNaN(numeric)) {
      // unitless multiplier
      lineHeightPx = fontSizePx * numeric;
    } else {
      lineHeightPx = fontSizePx * 1.2;
    }
  }

  // Add any vertical padding from the textarea (to match visual height)
  const paddingTop = parseFloat(cs.paddingTop) || 0;
  const paddingBottom = parseFloat(cs.paddingBottom) || 0;
  const borderTop = parseFloat(cs.borderTopWidth) || 0;
  const borderBottom = parseFloat(cs.borderBottomWidth) || 0;

  // Final single-line height (ceil to avoid fractional px jitter)
  const measured = Math.ceil(lineHeightPx + paddingTop + paddingBottom + borderTop + borderBottom);

  _singleLineHeightCached = measured;
  return _singleLineHeightCached;
}


function measureTextWidth(textarea, text) {
  const cs = window.getComputedStyle(textarea);
  const fontParts = [];
  if (cs.fontStyle) fontParts.push(cs.fontStyle);
  if (cs.fontVariant) fontParts.push(cs.fontVariant);
  if (cs.fontWeight) fontParts.push(cs.fontWeight);
  fontParts.push(cs.fontSize || '16px');
  fontParts.push(cs.fontFamily || 'sans-serif');
  _textMeasureCtx.font = fontParts.join(' ');
  const txt = (text !== undefined) ? text : (textarea.value || '');
  const metrics = _textMeasureCtx.measureText(txt);
  const w = metrics.width || 0;
  return w + 1.5; // very small static fudge
}

// =========== Optimized autoResizeAndLayout - prevents wrapping in single-line ===========
function autoResizeAndLayout(textarea) {
  const inputSection = document.getElementById("input-section");
  if (!textarea || !inputSection) return;

  const singleLineH = measureSingleLineHeight(textarea);
  const text = textarea.value;

  // Empty text -> always single line
  if (!text.trim()) {
    inputSection.classList.remove("multiline");
    textarea.style.height = (singleLineH + EXTRA_BUFFER) + "px";
    textarea.style.overflowY = "hidden";
    textarea.style.whiteSpace = "nowrap";
    return;
  }

  // Check if we have explicit line breaks first
  const hasLineBreaks = text.includes("\n");
  
  if (hasLineBreaks) {
    // Force multiline mode for explicit line breaks
    if (!inputSection.classList.contains("multiline")) {
      inputSection.classList.add("multiline");
    }
    textarea.style.whiteSpace = "pre-wrap";
    textarea.style.height = "auto";
    const targetH = Math.min(textarea.scrollHeight + EXTRA_BUFFER, MAX_TEXTAREA_HEIGHT);
    textarea.style.height = targetH + "px";
    textarea.style.overflowY = targetH >= MAX_TEXTAREA_HEIGHT ? "auto" : "hidden";
    return;
  }

  // For single-line text without line breaks
  const isCurrentlyMultiline = inputSection.classList.contains("multiline");
  
  if (!isCurrentlyMultiline) {
    // SINGLE-LINE MODE: Force no wrapping and measure if it overflows
    textarea.style.whiteSpace = "nowrap";
    textarea.style.height = (singleLineH + EXTRA_BUFFER) + "px";
    textarea.style.overflowY = "hidden";
    
    // Check if text is overflowing horizontally
    const isOverflowing = textarea.scrollWidth > textarea.clientWidth;
    
    if (isOverflowing) {
      // Switch to multiline mode
      inputSection.classList.add("multiline");
      textarea.style.whiteSpace = "pre-wrap";
      
      // Recalculate height for wrapped text
      textarea.style.height = "auto";
      const targetH = Math.min(textarea.scrollHeight + EXTRA_BUFFER, MAX_TEXTAREA_HEIGHT);
      textarea.style.height = targetH + "px";
      textarea.style.overflowY = targetH >= MAX_TEXTAREA_HEIGHT ? "auto" : "hidden";
    }
  } else {
    // MULTILINE MODE: Check if we should switch back to single-line
    textarea.style.whiteSpace = "pre-wrap";
    textarea.style.height = "auto";
    const targetH = Math.min(textarea.scrollHeight + EXTRA_BUFFER, MAX_TEXTAREA_HEIGHT);
    textarea.style.height = targetH + "px";
    textarea.style.overflowY = targetH >= MAX_TEXTAREA_HEIGHT ? "auto" : "hidden";
    
    // Test if this text would fit in single-line mode
    // Create a temporary measurement
    const tempSpan = document.createElement('span');
    tempSpan.style.font = window.getComputedStyle(textarea).font;
    tempSpan.style.visibility = 'hidden';
    tempSpan.style.position = 'absolute';
    tempSpan.style.whiteSpace = 'nowrap';
    tempSpan.textContent = text;
    document.body.appendChild(tempSpan);
    
    // Get available width for single-line mode
    const availableWidth = getSingleLineAvailableWidth(inputSection, textarea);
    const textWidth = tempSpan.getBoundingClientRect().width;
    
    document.body.removeChild(tempSpan);
    
    // If text would fit in single-line and we're close to single-line height, switch back
    const currentHeight = textarea.scrollHeight;
    const heightThreshold = singleLineH * 1.3; // Allow some buffer
    
    if (textWidth <= availableWidth && currentHeight <= heightThreshold) {
      // Switch back to single-line
      inputSection.classList.remove("multiline");
      textarea.style.whiteSpace = "nowrap";
      textarea.style.height = (singleLineH + EXTRA_BUFFER) + "px";
      textarea.style.overflowY = "hidden";
    }
  }
}

// Enhanced helper to get available width more accurately
function getSingleLineAvailableWidth(inputSection, textarea) {
  const left = inputSection.querySelector(".left-buttons");
  const right = inputSection.querySelector(".right-buttons");
  
  // Get the actual rendered widths
  const leftW = left ? left.getBoundingClientRect().width : 0;
  const rightW = right ? right.getBoundingClientRect().width : 0;
  
  // Get container width minus padding
  const containerRect = inputSection.getBoundingClientRect();
  const containerStyle = window.getComputedStyle(inputSection);
  const paddingLeft = parseFloat(containerStyle.paddingLeft) || 0;
  const paddingRight = parseFloat(containerStyle.paddingRight) || 0;
  
  // Get textarea padding
  const textareaStyle = window.getComputedStyle(textarea);
  const textareaPaddingLeft = parseFloat(textareaStyle.paddingLeft) || 0;
  const textareaPaddingRight = parseFloat(textareaStyle.paddingRight) || 0;
  
  const availableWidth = containerRect.width - paddingLeft - paddingRight - leftW - rightW - textareaPaddingLeft - textareaPaddingRight - 16; // Extra buffer
  
  return Math.max(100, availableWidth); // Minimum width to prevent issues
}

// Legacy auto-resize function for backward compatibility
function autoResize(textarea) {
  autoResizeAndLayout(textarea);
}

// Update send button state logic
function updateSendButton() {
  const sendButton = document.getElementById("send-button");
  const textInput = document.getElementById("text-input");
  const hasText = textInput.value.trim().length > 0;
  const hasImage = currentImageFile !== null;
  
  // Enable if there's text OR image
  sendButton.disabled = !hasText && !hasImage;
}

// Show image preview with proper cleanup
function showImagePreview(file) {
  const previewSection = document.getElementById("image-preview-section");
  const thumbnail = document.getElementById("image-thumbnail");
  const imageName = document.getElementById("image-name");
  
  // Clean up any existing object URL first
  if (thumbnail.src && thumbnail.src.startsWith('blob:')) {
    URL.revokeObjectURL(thumbnail.src);
  }
  
  // Create object URL for preview
  const objectUrl = URL.createObjectURL(file);
  thumbnail.src = objectUrl;
  imageName.textContent = file.name;
  
  // Show preview section
  previewSection.classList.add("visible");
  
  // Store current image file
  currentImageFile = file;
  
  // Update send button
  updateSendButton();
}

// Remove image preview with proper cleanup
function removeImagePreview() {
  const previewSection = document.getElementById("image-preview-section");
  const thumbnail = document.getElementById("image-thumbnail");
  
  // Hide preview section
  previewSection.classList.remove("visible");
  
  // Clean up object URL to prevent memory leaks
  if (thumbnail.src && thumbnail.src.startsWith('blob:')) {
    URL.revokeObjectURL(thumbnail.src);
  }
  thumbnail.src = '';
  
  // Clear current image file
  currentImageFile = null;
  
  // Reset file input
  document.getElementById("image-input").value = '';
  
  // Update send button
  updateSendButton();
}

// Send message (text and/or image)
async function sendMessage() {
  const textInput = document.getElementById("text-input");
  const text = textInput.value.trim();
  
  if (!text && !currentImageFile) {
    return;
  }

  try {
    // If we have an image, upload it first
    if (currentImageFile) {
      const formData = new FormData();
      formData.append("file", currentImageFile);
      
      // If we also have text, include it in the image upload
      if (text) {
        formData.append("text", text);
      }
      
      const response = await fetch(`${HTTP_URL}/upload-image/`, {
        method: "POST",
        body: formData
      });
      
      console.log("Image upload response:", await response.json());
    } else if (text) {
      // Send text only
      const payload = {
        message_text: text,
      };

      const response = await fetch(`${HTTP_URL}/send_message`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      console.log("Text message response:", await response.json());
    }
    
    // Clear input and image
    textInput.value = "";
    autoResizeAndLayout(textInput); // Reset layout to single line
    removeImagePreview();
    
    // Reset transcription mode
    if (isTranscriptionMode) {
      textInput.placeholder = "Type a message...";
      isTranscriptionMode = false;
    }
    
    // Update send button
    updateSendButton();
    
  } catch (error) {
    console.error("Failed to send message:", error);
  }
}

// Initialize UI handlers
export function initUI() {
  const textInput = document.getElementById("text-input");

  // inside initUI(), after const textInput = ...
if (document.fonts && document.fonts.ready) {
  document.fonts.ready.then(() => {
    // Clear cached single-line height and re-layout once fonts settle
    _singleLineHeightCached = null;
    autoResizeAndLayout(textInput);
  }).catch(() => {
    // ignore errors; best-effort
    _singleLineHeightCached = null;
    autoResizeAndLayout(textInput);
  });
}

  const micButton = document.getElementById("mic-button");
  const imageInput = document.getElementById("image-input");
  const sendButton = document.getElementById("send-button");
  const removeImageBtn = document.getElementById("remove-image-btn");

  // Enhanced input event handler with dynamic layout
  textInput.addEventListener("input", () => {
    autoResizeAndLayout(textInput);
    updateSendButton();
  });

  // Handle Enter key for sending (Shift+Enter for new line)
  textInput.addEventListener("keydown", async (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!sendButton.disabled) {
        await sendMessage();
      }
    }
  });

  // Handle window resize to recalculate layout
  window.addEventListener("resize", () => {
    // Clear cached single line height on resize
    _singleLineHeightCached = null;
    // Small delay to ensure proper calculation after resize
    setTimeout(() => {
      autoResizeAndLayout(textInput);
    }, 100);
  });

  // Mic button = mute toggle (desktop ASR + browser voice respect /mute).
  micButton.title = "Mute microphone";
  micButton.addEventListener("click", async () => {
    await toggleMicMute();
  });

  // Video / camera button: stream frames to LLaVA via /vision/frame
  const videoButton = document.getElementById("video-button");
  videoButton.title = "Camera — LLaVA sees you while this is on";
  videoButton.addEventListener("click", async () => {
    if (!isVideoStreaming()) {
      try {
        await startVideoStream();
        videoButton.innerHTML = `
          <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="#ff5555" stroke-width="2">
            <rect x="6" y="6" width="12" height="12" rx="2"/>
          </svg>
        `;
        videoButton.title = "Stop camera (LLaVA)";
      } catch (err) {
        console.error("Camera access denied:", err);
        alert("Could not access camera. Please check your permissions.");
      }
    } else {
      stopVideoStream();
      videoButton.innerHTML = `
        <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="m22 8-6 4 6 4V8Z"/>
          <rect x="2" y="6" width="14" height="12" rx="2"/>
        </svg>
      `;
      videoButton.title = "Camera — LLaVA sees you while this is on";
    }
  });

  // Send button click handler
  sendButton.addEventListener("click", async () => {
    if (!sendButton.disabled) {
      await sendMessage();
    }
  });

  // Image upload
  imageInput.addEventListener("change", async () => {
    const file = imageInput.files[0];
    if (file) {
      showImagePreview(file);
    }
  });

  // Remove image button handler
  removeImageBtn.addEventListener("click", () => {
    removeImagePreview();
  });

  // Initial send button state and layout
  updateSendButton();
  autoResizeAndLayout(textInput);
}

// ====== Mic mute (icon toggles mute; desktop ASR reads POST /mute) ======
let micMuted = false;

const MIC_ICON_ON = `
  <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
    <path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/>
    <path d="M19 10v1a7 7 0 0 1-14 0v-1"/>
    <path d="M12 18v4"/>
    <path d="M8 22h8"/>
  </svg>`;

const MIC_ICON_MUTED = `
  <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="#ff5555" stroke-width="2">
    <path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/>
    <path d="M19 10v1a7 7 0 0 1-14 0v-1"/>
    <path d="M12 18v4"/>
    <path d="M8 22h8"/>
    <line x1="4" y1="4" x2="20" y2="20"/>
  </svg>`;

async function toggleMicMute() {
  const micButton = document.getElementById("mic-button");
  micMuted = !micMuted;
  micButton.innerHTML = micMuted ? MIC_ICON_MUTED : MIC_ICON_ON;
  micButton.title = micMuted ? "Unmute microphone" : "Mute microphone";
  try {
    await fetch(`${HTTP_URL}/mute`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ muted: micMuted }),
    });
    console.log(micMuted ? "Mic muted" : "Mic unmuted");
  } catch (err) {
    console.error("Failed to set mute on server:", err);
  }
}

// Toggle UI visibility.
//
// Registered at MODULE SCOPE, not on DOMContentLoaded. This file is a deferred
// `type="module"` script, so the DOM is already parsed when it runs — but its
// import chain (config.js) contains a TOP-LEVEL AWAIT (`askServerCharacter()`,
// a fetch to the server's /health with a 2s timeout). That suspends this module's
// evaluation, and DOMContentLoaded can fire while it is still suspended: the
// listener would then be attached to an event that already happened and the
// button would be dead until the page was reloaded — intermittently, because
// it was a race against how fast the server answered. Attaching directly at module
// scope (the DOM is already parsed by then) cannot lose that
// race. The subtitle button in app.js was always registered this way, which is
// why it never showed the symptom. If you add a button, follow this pattern.
const toggleButton = document.getElementById('toggle-ui-button');
if (toggleButton) {
  const body = document.body;
  let isHidden = false;

  toggleButton.addEventListener('click', () => {
    isHidden = !isHidden;
    
    if (isHidden) {
      body.classList.add('ui-hidden');
      // Change icon to show/eye icon
      toggleButton.innerHTML = `
        <svg class="icon" viewBox="0 0 24 24">
          <path d="M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zM12 17c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5zm0-8c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3z"/>
        </svg>
      `;
    } else {
      body.classList.remove('ui-hidden');
      // Change back to X icon
      toggleButton.innerHTML = `
        <svg class="icon" viewBox="0 0 24 24">
          <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/>
        </svg>
      `;
    }
  });
}
