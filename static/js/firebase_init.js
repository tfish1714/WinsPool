// static/js/firebase_init.js
import { initializeApp } from "firebase/app";
import { initializeFirestore, persistentLocalCache, persistentMultipleTabManager } from "firebase/firestore";

/**
 * Initializes Firebase dynamically by fetching the public configuration
 * from the backend. This avoids hardcoding keys in the source code.
 */
async function initializeFirebase() {
    try {
        const response = await fetch('/api/config/firebase');
        if (!response.ok) throw new Error("Failed to fetch Firebase configuration");

        const firebaseConfig = await response.json();

        // Initialize Firebase
        const app = initializeApp(firebaseConfig);

        // Initialize Firestore with persistent cache enabled
        const db = initializeFirestore(app, {
            localCache: persistentLocalCache({
                tabManager: persistentMultipleTabManager()
            })
        });

        console.log("[Firebase] Firestore initialized dynamically with local persistence.");
        return db;
    } catch (error) {
        console.error("[Firebase] Initialization failed:", error);
        throw error;
    }
}

// Export a promise that resolves to the DB instance
export const dbPromise = initializeFirebase();

/**
 * Helper to get the DB instance once it's ready.
 */
export async function getDb() {
    return await dbPromise;
}
