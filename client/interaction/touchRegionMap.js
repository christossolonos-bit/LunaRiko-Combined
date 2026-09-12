import { HTTP_URL } from '../config.js';

/**
 * Maps a VRM bone name to a general body region.
 * Shared between desktop click detector and VR touch detector.
 */
export function mapBoneToRegion(boneName) {
    const name = boneName.toLowerCase();

    // Facial regions
    if (name.includes("faceeye")) return "face";

    // Cat ears
    if (name.includes("l_catear")) return "left_cat_ear";
    if (name.includes("r_catear")) return "right_cat_ear";

    // Head and neck region
    if (name.includes("head")) return "head";
    if (name.includes("neck")) return "neck";

    // Torso regions
    if (name.includes("upperchest") || name.includes("chest")) return "chest";
    if (name.includes("bust")) return "bust";
    if (name.includes("spine")) return "belly";

    // Arms
    if (name.includes("l_shoulder")) return "left_shoulder";
    if (name.includes("r_shoulder")) return "right_shoulder";
    if (name.includes("l_upperarm") || name.includes("l_lowerarm"))
        return "left_arm";
    if (name.includes("r_upperarm") || name.includes("r_lowerarm"))
        return "right_arm";

    // Hands and fingers
    if (
        name.includes("l_hand") ||
        name.includes("l_index") ||
        name.includes("l_little") ||
        name.includes("l_middle") ||
        name.includes("l_ring") ||
        name.includes("l_thumb")
    )
        return "left_hand";
    if (
        name.includes("r_hand") ||
        name.includes("r_index") ||
        name.includes("r_little") ||
        name.includes("r_middle") ||
        name.includes("r_ring") ||
        name.includes("r_thumb")
    )
        return "right_hand";

    // Hips and legs
    if (name.includes("hips")) return "hips";
    if (name.includes("l_upperleg")) return "left_thigh";
    if (name.includes("r_upperleg")) return "right_thigh";
    if (name.includes("l_lowerleg")) return "left_shin";
    if (name.includes("r_lowerleg")) return "right_shin";

    // Feet and toes
    if (name.includes("l_foot") || name.includes("l_toe")) return "left_foot";
    if (name.includes("r_foot") || name.includes("r_toe")) return "right_foot";

    // Special features
    if (name.includes("foxtail")) return "tail";
    if (name.includes("hair")) return "hair";
    if (name.includes("coatskirt")) return "clothing";

    return "body";
}

/**
 * Send click/touch interaction to backend.
 */
export async function sendClickInteraction(boneName, region) {
    const payload = {
        type: "click_interaction",
        bone: boneName,
        region: region,
    };
    try {
        const res = await fetch(`${HTTP_URL}/send_click_interaction`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        console.log("Talk response:", await res.json());
    } catch (error) {
        console.error("Failed to send interaction:", error);
    }
}
