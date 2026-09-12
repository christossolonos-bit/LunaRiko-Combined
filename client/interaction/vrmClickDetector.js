// vrmClickDetector.js - ENHANCED VERSION with Click Effects
import * as THREE from "three";

import { VRM_PATH, WS_URL, HTTP_URL } from "../config.js";
import { mapBoneToRegion, sendClickInteraction } from "./touchRegionMap.js";

let ws;

// Audio for click sound effect
let clickAudio = null;

// Initialize click sound
function initClickSound() {
    clickAudio = new Audio("./sounds/click-sound.wav"); // YOU NEED TO ADD THIS FILE
    clickAudio.volume = 1; // Adjust volume as needed
    clickAudio.preload = "auto";
}

// Play click sound effect
function playClickSound() {
    if (clickAudio) {
        clickAudio.currentTime = 0; // Reset to start
        clickAudio.play().catch((e) => console.log("Audio play failed:", e));
    }
}

// Create and animate click effect
function createClickEffect(x, y) {
    const effect = document.createElement("div");
    effect.className = "vrm-click-effect-pulse"; // Using the pulse version
    effect.style.left = x + "px";
    effect.style.top = y + "px";

    document.body.appendChild(effect);

    // Remove effect after animation completes
    setTimeout(() => {
        if (effect.parentNode) {
            effect.parentNode.removeChild(effect);
        }
    }, 500);
}

export function initVRMClickDetector(renderer, camera, vrm, onRegionClick) {
    // Initialize click sound
    initClickSound();

    const raycaster = new THREE.Raycaster();
    const mouse = new THREE.Vector2();

    // Enhanced cooldown and drag detection
    let lastClickTime = 0;
    let mouseDownTime = 0;
    let mouseDownPosition = { x: 0, y: 0 };
    let isDragging = false;
    let CLICK_COOLDOWN = 500;
    const DRAG_THRESHOLD = 5; // pixels
    const MAX_CLICK_DURATION = 200; // milliseconds for quick click

    // Performance optimizations
    raycaster.firstHitOnly = true;

    // Pre-cache all skinned meshes in VRM for better performance
    const meshes = [];
    const boundingBoxes = new Map();

    vrm.scene.traverse((obj) => {
        if (obj.isSkinnedMesh) {
            meshes.push(obj);
            if (obj.geometry.boundingBox === null) {
                obj.geometry.computeBoundingBox();
            }
            boundingBoxes.set(obj, obj.geometry.boundingBox);
        }
    });

    // Optimized intersection checking with early bailouts
    function getIntersectionWithOptimization(mouse, camera) {
        raycaster.setFromCamera(mouse, camera);

        const frustum = new THREE.Frustum();
        const matrix = new THREE.Matrix4().multiplyMatrices(
            camera.projectionMatrix,
            camera.matrixWorldInverse,
        );
        frustum.setFromProjectionMatrix(matrix);

        const visibleMeshes = meshes.filter((mesh) => {
            const boundingBox = boundingBoxes.get(mesh);
            if (!boundingBox) return true;

            const worldBoundingBox = boundingBox
                .clone()
                .applyMatrix4(mesh.matrixWorld);
            return frustum.intersectsBox(worldBoundingBox);
        });

        return raycaster.intersectObjects(visibleMeshes, false);
    }

    // Enhanced UI detection - includes hidden state check
    function isClickOnUI(event) {
        const element = document.elementFromPoint(event.clientX, event.clientY);
        if (!element) return false;

        // Check if UI is hidden
        const isUIHidden = document.body.classList.contains("ui-hidden");

        // If UI is hidden, only the toggle button should be clickable
        if (isUIHidden) {
            const toggleButton = element.closest(".toggle-button");
            return !!toggleButton;
        }

        // Normal UI detection when UI is visible
        const uiSelectors = [
            ".ui-container",
            ".toggle-button",
            "#text-input",
            "#mic-button",
            ".image-upload",
            "#image-input",
            "button",
            "input",
            "label",
        ];

        return uiSelectors.some((selector) => {
            try {
                return element.matches(selector) || element.closest(selector);
            } catch (e) {
                return false;
            }
        });
    }

    // Mouse down handler - track start of potential click
    function handleMouseDown(event) {
        mouseDownTime = Date.now();
        mouseDownPosition = { x: event.clientX, y: event.clientY };
        isDragging = false;
    }

    // Mouse move handler - detect dragging
    function handleMouseMove(event) {
        if (mouseDownTime > 0) {
            const deltaX = Math.abs(event.clientX - mouseDownPosition.x);
            const deltaY = Math.abs(event.clientY - mouseDownPosition.y);

            if (deltaX > DRAG_THRESHOLD || deltaY > DRAG_THRESHOLD) {
                isDragging = true;
            }
        }
    }

    // Mouse up handler - process actual clicks
    function handleMouseUp(event) {
        const currentTime = Date.now();
        const clickDuration = currentTime - mouseDownTime;

        // Reset tracking
        const wasDragging = isDragging;
        mouseDownTime = 0;
        isDragging = false;

        // Check if this was a valid click (not a drag, not too long)
        if (wasDragging || clickDuration > MAX_CLICK_DURATION) {
            console.log(
                "🚫 Drag or long press detected - ignoring VRM interaction",
            );
            return;
        }

        // Check cooldown
        if (currentTime - lastClickTime < CLICK_COOLDOWN) {
            console.log(
                `🚫 Click ignored - cooldown active (${CLICK_COOLDOWN - (currentTime - lastClickTime)}ms remaining)`,
            );
            return;
        }

        // Check if clicking on UI
        if (isClickOnUI(event)) {
            console.log("🖱️ Click on UI detected - ignoring VRM interaction");
            return;
        }

        // Check if VRM exists
        if (!vrm) {
            console.warn("🚫 VRM not loaded");
            return;
        }

        // Prevent default and stop propagation
        event.preventDefault();
        event.stopPropagation();

        // Convert mouse coordinates
        const rect = renderer.domElement.getBoundingClientRect();
        mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
        mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

        // Perform intersection test
        const intersects = getIntersectionWithOptimization(mouse, camera);

        if (intersects.length > 0) {
            const hit = intersects[0];
            const mesh = hit.object;
            const geometry = mesh.geometry;

            // Validation
            if (
                !geometry.attributes.skinIndex ||
                !geometry.attributes.skinWeight
            ) {
                console.warn("🚫 Mesh has no skinning data");
                return;
            }

            try {
                // Get the closest vertex index from the face
                const face = hit.face;
                const point = hit.point;

                const vertices = [face.a, face.b, face.c];
                let closestIndex = face.a;
                let minDistance = Infinity;

                const position = geometry.attributes.position;
                const tempVertex = new THREE.Vector3();

                for (const vertexIndex of vertices) {
                    tempVertex.fromBufferAttribute(position, vertexIndex);
                    mesh.localToWorld(tempVertex);
                    const distance = tempVertex.distanceTo(point);

                    if (distance < minDistance) {
                        minDistance = distance;
                        closestIndex = vertexIndex;
                    }
                }

                // Get bone information
                const skinIndex =
                    geometry.attributes.skinIndex.getX(closestIndex);
                const bone = mesh.skeleton.bones[skinIndex];
                const boneName = bone ? bone.name : "Unknown bone";
                const region = mapBoneToRegion(boneName);

                console.log(
                    `🎯 VRM clicked! Bone: ${boneName} → Region: ${region}`,
                );

                // Play effects
                playClickSound();
                createClickEffect(event.clientX, event.clientY);

                // Send interaction
                sendClickInteraction(boneName, region);

                // Update cooldown
                lastClickTime = currentTime;

                // Execute callback
                if (onRegionClick) {
                    onRegionClick(region, boneName);
                }
            } catch (error) {
                console.error("🚫 Error processing VRM click:", error);
            }
        } else {
            console.log("🖱️ Clicked but no VRM intersection found");
        }
    }

    // Add event listeners
    window.addEventListener("mousedown", handleMouseDown, {
        capture: true,
        passive: false,
    });
    window.addEventListener("mousemove", handleMouseMove, {
        capture: true,
        passive: true,
    });
    window.addEventListener("mouseup", handleMouseUp, {
        capture: true,
        passive: false,
    });

    // Expose cooldown adjustment function
    window.setVRMClickCooldown = (ms) => {
        CLICK_COOLDOWN = Math.max(0, ms);
        console.log(`🕒 VRM click cooldown set to ${CLICK_COOLDOWN}ms`);
    };

    console.log(`✅ Enhanced VRM Click Detector initialized`);
    console.log(`📊 Cached ${meshes.length} skinned meshes for raycasting`);
    console.log(`🎵 Click sound effect ready`);
}

// mapBoneToRegion and sendClickInteraction imported from touchRegionMap.js
