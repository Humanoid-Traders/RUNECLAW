# RUNECLAW on Android — the TWA path

The website is already an **installable PWA** (manifest + service worker with
web-push). The fastest, zero-duplication route to a real Play Store app is a
**Trusted Web Activity (TWA)**: a thin Android wrapper that opens the live
site fullscreen. The web app IS the app — every web deploy updates the app
instantly, no store re-submission for content changes.

## Why TWA (and not a rewrite)

- **One codebase.** Dashboard, Arena, Guardian — everything ships to Android
  the moment the host redeploys.
- **Web push already works** (sw.js) and notifications surface like native.
- **App shortcuts** (long-press icon → Dashboard / Paper Arena / Guardian /
  Signals) come from `manifest.json` — already configured.
- A Kotlin/React-Native rewrite would fork every §4-sensitive surface and
  double the audit area for zero user-visible gain.

## One-time setup (operator)

1. **Install Bubblewrap** (Google's TWA CLI):
   ```bash
   npm i -g @bubblewrap/cli
   bubblewrap init --manifest https://<your-domain>/manifest.json
   ```
   Pick an application id, e.g. `com.humanoidtraders.runeclaw`.

2. **Build the signed bundle:**
   ```bash
   bubblewrap build
   ```
   This creates the signing keystore (BACK IT UP) and an `.aab` for Play
   Console plus an `.apk` for sideload testing.

3. **Publish the Digital Asset Link** so Android trusts the site and hides
   the browser chrome. Get the SHA-256 of your signing cert:
   ```bash
   keytool -list -v -keystore android.keystore | grep SHA256
   ```
   Then set two env vars on the web host and redeploy:
   ```
   ANDROID_PACKAGE=com.humanoidtraders.runeclaw
   ANDROID_CERT_SHA256=AA:BB:...   # the SHA-256 fingerprint
   ```
   The server then serves `/.well-known/assetlinks.json` automatically
   (an unconfigured host answers 404 — honest, never an empty statement).
   Verify with:
   ```bash
   curl https://<your-domain>/.well-known/assetlinks.json
   ```

4. **Upload the `.aab`** to the Play Console (Finance category — expect the
   standard finance-app review questions; the §4 posture — no custody, keys
   withdrawal-disabled, paper-first — is the story to tell).

## Caveats

- The TWA needs a **stable HTTPS domain** — the assetlinks fingerprint binds
  to it. Moving domains means updating the manifest + assetlinks together.
- Play policy for finance apps varies by region; the Paper Arena (virtual
  funds only) is the safest first storefront angle.
- iOS: the same PWA installs from Safari ("Add to Home Screen") today; a
  store presence there would need a different wrapper (out of scope here).
