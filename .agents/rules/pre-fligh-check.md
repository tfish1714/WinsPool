---
trigger: always_on
---

# 🚨 CORE AGENT DIRECTIVES 🚨


## 1. Professional & Production-Ready Standards
* **Action:** All code, comments, and documentation must be strictly professional and "senior-engineer" level. 
* **Action:** You are forbidden from using emojis in code, comments, or documentation.
* **Action:** If UI icons are required, use only established free-use icon libraries (e.g., FontAwesome, Lucide, or Heroicons).
* **Goal:** Ensure the codebase is clean, high-signal, and suitable for a professional production environment.

## 2. The Pre-Flight Check (Read Before Writing)
* **Action:** Before generating any code, modifying files, or proposing architectural changes, you MUST review the `docs/` directory to understand the current architecture and existing endpoints.
* **Goal:** Prevent hallucinating duplicate functions and ensure new code seamlessly integrates with the established project structure.

## 3. Anti-Truncation & Preservation (Zero-Deletion Policy)
* **Action:** You are strictly forbidden from using placeholders (e.g., `// ... existing code ...`). 
* **Action:** When modifying a file, you must output the entire, fully runnable file, or an exact, targeted Git-style diff.
* **Goal:** Your modifications must be strictly additive. NEVER silently delete, truncate, or refactor existing features, logic, or UI elements to make room for new code unless explicitly commanded to do so.

## 4. Test-Driven Regression Prevention
* **Action:** Every new feature or logic change must include corresponding pytest updates.
* **Action:** Ensure new tests cover happy paths and edge cases.
* **Goal:** Use automated testing to mathematically prove that your new code functions correctly and that no pre-existing features were inadvertently broken or removed in the process.

## 5. Auto-Documentation
* **Action:** Automatically update the relevant markdown files in `docs/` to reflect changes after a successful implementation.