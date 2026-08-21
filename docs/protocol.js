/* mattend hop 2: the phone's half of the relay.
 *
 *     Z*_s = P_s( Z*_{CID,GT} || UUID || Cap_T )
 *
 * This must stay byte-for-byte identical to server/crypto.py and
 * server/codec.py -- PC (in) runs the exact inverse. If you change a struct
 * field, a domain label or the tag length here, change it there too and bump
 * PROTO_VERSION on both sides.
 *
 * The phone never decrypts the inner blob: it is opaque relay cargo. Only the
 * two lab PCs hold the pc secret, so a phone cannot learn C_ID or Gen_T, and
 * cannot mint a source QR of its own.
 */
(function (global) {
  "use strict";

  // Must equal app_secret_hex in server/config.json.
  const APP_SECRET_HEX = "9dcb48b53200a5b764ca6f605350c4aa30794585fa484d847aafb35d2faef304";

  const PROTO_VERSION = 1;
  const TAG_LEN = 6;
  const INNER_BLOB_LEN = 15;
  const DOMAIN_MOBILE = "mobile";
  const B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

  function hexToBytes(hex) {
    if (!/^[0-9a-fA-F]{64}$/.test(hex)) {
      throw new Error("APP_SECRET_HEX must be 64 hex characters");
    }
    const out = new Uint8Array(32);
    for (let i = 0; i < 32; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
    return out;
  }

  function b32encode(bytes) {
    let bits = 0, value = 0, out = "";
    for (let i = 0; i < bytes.length; i++) {
      value = (value << 8) | bytes[i];
      bits += 8;
      while (bits >= 5) {
        out += B32[(value >>> (bits - 5)) & 31];
        bits -= 5;
      }
    }
    if (bits > 0) out += B32[(value << (5 - bits)) & 31];
    return out;
  }

  function b32decode(text) {
    const clean = text.trim().toUpperCase().replace(/[=\s]/g, "");
    let bits = 0, value = 0;
    const out = [];
    for (const ch of clean) {
      const index = B32.indexOf(ch);
      if (index < 0) throw new Error("not a mattend QR (bad base32 character)");
      value = (value << 5) | index;
      bits += 5;
      if (bits >= 8) {
        out.push((value >>> (bits - 8)) & 255);
        bits -= 8;
      }
    }
    return new Uint8Array(out);
  }

  async function hmac(key, message) {
    const imported = await crypto.subtle.importKey(
      "raw", key, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]
    );
    return new Uint8Array(await crypto.subtle.sign("HMAC", imported, message));
  }

  const subkeyCache = new Map();
  async function subkey(master, label) {
    if (!subkeyCache.has(label)) subkeyCache.set(label, await hmac(master, new TextEncoder().encode(label)));
    return subkeyCache.get(label);
  }

  async function keystream(kEnc, tag, n) {
    const out = new Uint8Array(n);
    let filled = 0, counter = 0;
    while (filled < n) {
      const block = new Uint8Array(tag.length + 1);
      block.set(tag, 0);
      block[tag.length] = counter++;
      const digest = await hmac(kEnc, block);
      const take = Math.min(digest.length, n - filled);
      out.set(digest.subarray(0, take), filled);
      filled += take;
    }
    return out;
  }

  /* SIV: the tag is both the authenticator and the nonce. */
  async function pack(master, domain, plaintext) {
    const kMac = await subkey(master, domain + "/mac");
    const kEnc = await subkey(master, domain + "/enc");
    const tag = (await hmac(kMac, plaintext)).subarray(0, TAG_LEN);
    const stream = await keystream(kEnc, tag, plaintext.length);
    const blob = new Uint8Array(TAG_LEN + plaintext.length);
    blob.set(tag, 0);
    for (let i = 0; i < plaintext.length; i++) blob[TAG_LEN + i] = plaintext[i] ^ stream[i];
    return blob;
  }

  function uuidToBytes(text) {
    const hex = text.replace(/-/g, "");
    if (!/^[0-9a-fA-F]{32}$/.test(hex)) throw new Error("bad device UUID");
    const out = new Uint8Array(16);
    for (let i = 0; i < 16; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
    return out;
  }

  function u32(value) {
    return new Uint8Array([(value >>> 24) & 255, (value >>> 16) & 255, (value >>> 8) & 255, value & 255]);
  }

  /* Plaintext layout, mirroring codec.encode_outer:
   *     version(1) | inner blob(15) | uuid(16) | cap_t(4)  = 36 bytes
   * which packs to 42 bytes and Base32s to 68 alphanumeric characters -- a
   * QR version 3 at ECC level L. */
  function encodeOuter(innerBlob, deviceUUID, capT) {
    if (innerBlob.length !== INNER_BLOB_LEN) {
      throw new Error("that is not a mattend source QR");
    }
    const out = new Uint8Array(1 + INNER_BLOB_LEN + 16 + 4);
    out[0] = PROTO_VERSION;
    out.set(innerBlob, 1);
    out.set(uuidToBytes(deviceUUID), 1 + INNER_BLOB_LEN);
    out.set(u32(capT), 1 + INNER_BLOB_LEN + 16);
    return out;
  }

  /**
   * Fuse a scanned source QR with this device's identity and capture time.
   * @returns {Promise<{text: string, capT: number}>} payload for the response QR.
   */
  async function makeResponseQR(sourceText, deviceUUID, capT) {
    const master = hexToBytes(APP_SECRET_HEX);
    const capture = capT === undefined ? Math.floor(Date.now() / 1000) : capT;
    const plaintext = encodeOuter(b32decode(sourceText), deviceUUID, capture);
    return { text: b32encode(await pack(master, DOMAIN_MOBILE, plaintext)), capT: capture };
  }

  global.MattendProtocol = { makeResponseQR, b32encode, b32decode, PROTO_VERSION, INNER_BLOB_LEN };
})(window);
