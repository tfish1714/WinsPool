import { getDb } from "./firebase_init.js";
import {
    loadBundle,
    collection,
    query,
    where,
    getDocsFromCache,
    getNamedQueryFromCache
} from "firebase/firestore";

/**
 * BundleService handles loading Firestore Data Bundles and 
 * executing strictly cached queries for historical seasons.
 */
export const BundleService = {

    /**
     * Loads a season's data bundle into the local Firestore cache.
     * @param {number|string} year The season year to load.
     */
    async loadSeasonBundle(year) {
        const bundleUrl = `/static/bundles/${year}_season_bundle.txt`;
        console.log(`[BundleService] Fetching bundle for season ${year}...`);

        try {
            const response = await fetch(bundleUrl);
            if (!response.ok) throw new Error(`Bundle not found: ${bundleUrl}`);

            const bundleData = await response.body; // loadBundle accepts a stream
            const db = await getDb();
            await loadBundle(db, bundleData);

            console.log(`[BundleService] Season ${year} bundle loaded into local cache.`);
            return true;
        } catch (error) {
            console.error(`[BundleService] Error loading bundle for ${year}:`, error);
            return false;
        }
    },

    /**
     * Executes an optimized query strictly against the local Firestore cache.
     * This ensures ZERO READ COSTS for historical data.
     * @param {string} collectionName The Firestore collection to query.
     * @param {number|string} year The season year.
     */
    async getSeasonDataFromCache(collectionName, year) {
        console.log(`[BundleService] Querying cache for ${collectionName} (Season ${year})...`);

        try {
            const seasonValue = typeof year === "string" ? parseInt(year, 10) : year;
            const yearField = collectionName === "weekly_recaps" ? "year" : "season";

            const db = await getDb();
            const q = query(
                collection(db, collectionName),
                where(yearField, "==", seasonValue)
            );

            const snapshot = await getDocsFromCache(q);
            return snapshot.docs.map(doc => doc.data());
        } catch (error) {
            console.warn(`[BundleService] Cache miss or error for ${collectionName}:`, error);
            return [];
        }
    },

    /**
     * Retrieves data using a named query from the bundle.
     * @param {string} queryName The name given to the query when the bundle was created.
     */
    async getNamedQueryResult(queryName) {
        try {
            const db = await getDb();
            const q = await getNamedQueryFromCache(db, queryName);
            if (!q) return [];

            const snapshot = await getDocsFromCache(q);
            return snapshot.docs.map(doc => doc.data());
        } catch (error) {
            console.error(`[BundleService] Error fetching named query ${queryName}:`, error);
            return [];
        }
    }
};

// Auto-inject into window for global access if needed
window.BundleService = BundleService;
