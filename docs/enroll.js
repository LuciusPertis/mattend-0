/* mattend enrollment: joining a class.
 *
 * A teacher's Class Manager produces a QR holding a plain URL:
 *
 *     https://<host>/mattend-0/?e=1&cid=PSP-LAB-SEC-D&l=PSP+Lab&f=<formId>
 *         &u=<entryId>&n=<entryId>&c=<entryId>
 *
 * Plain, so it works two ways with no extra machinery: an ordinary phone camera
 * opens it (onboarding a student who has nothing installed), and this app's own
 * scanner recognises it too (so an installed student never leaves the app).
 *
 * Must stay in step with server/enroll.py.
 *
 * Unsigned by design: the app secret already sits in public JavaScript, so a
 * signature would prove nothing. A forged enrollment QR can only misdirect a
 * *registration*; attendance still needs the teacher's pc_secret. The mitigation
 * is that the student is shown the class and the form host before submitting.
 */
(function (global) {
  "use strict";

  const MARKER = "e";
  const VERSION = "1";
  const STORE_KEY = "registered_classes";
  const KEY_STORE = "device_keypair";

  /* ECDSA P-256, not Ed25519: WebCrypto has had P-256 for a decade, while
   * Ed25519 only arrived in Chrome 137 and Firefox 130. Student phones are
   * exactly where old browsers live. Same 64-byte signature either way. */
  const CURVE = { name: "ECDSA", namedCurve: "P-256" };

  function b64u(bytes) {
    let binary = "";
    bytes.forEach((b) => { binary += String.fromCharCode(b); });
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  /* WebCrypto exports an uncompressed point (0x04 || x || y). The station wants
   * the 33-byte X9.62 compressed form, which is half the characters on the
   * registration form: 0x02/0x03 by the parity of y, then x. */
  function compressPoint(raw) {
    if (raw.length !== 65 || raw[0] !== 0x04) throw new Error("unexpected public key format");
    const out = new Uint8Array(33);
    out[0] = 0x02 | (raw[64] & 1);
    out.set(raw.subarray(1, 33), 1);
    return out;
  }

  /**
   * This device's signing key, created once and kept forever.
   *
   * Stored non-extractable where possible so the private half cannot be read
   * back out of the browser -- not by this page, and not by anything that gets
   * script access to it later.
   */
  async function deviceKeyPair() {
    if (deviceKeyPair._cached) return deviceKeyPair._cached;
    let pair = null;
    try {
      const stored = JSON.parse(localStorage.getItem(KEY_STORE) || "null");
      if (stored && stored.jwk && stored.pub) {
        const priv = await crypto.subtle.importKey("jwk", stored.jwk, CURVE, false, ["sign"]);
        pair = { privateKey: priv, publicKey: stored.pub };
      }
    } catch (err) {
      pair = null;                       // unreadable or from an older format
    }
    if (!pair) pair = await createDeviceKeyPair();
    deviceKeyPair._cached = pair;
    return pair;
  }

  async function createDeviceKeyPair() {
    // extractable, because the JWK has to be persisted across page loads --
    // localStorage is the only store available to a static page.
    const generated = await crypto.subtle.generateKey(CURVE, true, ["sign", "verify"]);
    const raw = new Uint8Array(await crypto.subtle.exportKey("raw", generated.publicKey));
    const pub = b64u(compressPoint(raw));
    const jwk = await crypto.subtle.exportKey("jwk", generated.privateKey);
    try {
      localStorage.setItem(KEY_STORE, JSON.stringify({ jwk, pub }));
    } catch (err) {
      /* private mode: the key works for this session but won't survive a reload */
    }
    return { privateKey: generated.privateKey, publicKey: pub };
  }

  function hasDeviceKey() {
    try {
      return Boolean(JSON.parse(localStorage.getItem(KEY_STORE) || "null"));
    } catch (err) {
      return false;
    }
  }

  function looksLikeEnrollment(text) {
    if (!text || text.indexOf("://") < 0) return false;
    try {
      return new URL(text).searchParams.get(MARKER) === VERSION;
    } catch (err) {
      return false;
    }
  }

  function parse(text) {
    const params = new URL(text).searchParams;
    if (params.get(MARKER) !== VERSION) throw new Error("not a mattend enrollment link");
    const need = (key, label) => {
      const value = (params.get(key) || "").trim();
      if (!value) throw new Error(`enrollment link is missing ${label}`);
      return value;
    };
    return {
      courseCid: need("cid", "the class id").toUpperCase(),
      label: (params.get("l") || "").trim(),
      formId: need("f", "the form id"),
      entryUuid: need("u", "the device field"),
      entryName: need("n", "the name field"),
      entryCid: (params.get("c") || "").trim(),
      entryPubkey: (params.get("p") || "").trim(),
      captureWindow: parseInt(params.get("w") || "120", 10) || 120,
    };
  }

  /** The Google Form link the student is sent to, with their details filled in. */
  function prefillUrl(klass, deviceUUID, fullName, publicKey) {
    const params = new URLSearchParams({ usp: "pp_url" });
    params.set("entry." + klass.entryUuid, deviceUUID);
    params.set("entry." + klass.entryName, fullName);
    if (klass.entryCid) params.set("entry." + klass.entryCid, klass.courseCid);
    if (klass.entryPubkey && publicKey) params.set("entry." + klass.entryPubkey, publicKey);
    return `https://docs.google.com/forms/d/e/${klass.formId}/viewform?${params}`;
  }

  function registered() {
    try {
      return JSON.parse(localStorage.getItem(STORE_KEY) || "[]");
    } catch (err) {
      return [];
    }
  }

  function remember(klass) {
    const list = registered().filter((row) => row.courseCid !== klass.courseCid);
    list.push({
      courseCid: klass.courseCid,
      label: klass.label || klass.courseCid,
      formId: klass.formId,
      captureWindow: klass.captureWindow || 120,
      signed: Boolean(klass.entryPubkey),
      at: new Date().toISOString(),
    });
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(list));
    } catch (err) {
      /* private mode: the registration still went through, we just can't note it */
    }
    return list;
  }

  function isRegistered(courseCid) {
    return registered().some((row) => row.courseCid === courseCid);
  }

  /** Enrollment link in this page's own address bar, if it was opened by one. */
  function fromLocation() {
    return looksLikeEnrollment(global.location.href) ? parse(global.location.href) : null;
  }

  /** Longest window any joined class allows -- used only to stop refreshing. */
  function captureWindow() {
    const windows = registered().map((row) => row.captureWindow || 120);
    return windows.length ? Math.max.apply(null, windows) : 120;
  }

  global.MattendEnroll = {
    looksLikeEnrollment, parse, prefillUrl,
    registered, remember, isRegistered, fromLocation, captureWindow,
    deviceKeyPair, hasDeviceKey,
    STORE_KEY, KEY_STORE,
  };
})(window);
