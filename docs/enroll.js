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
    };
  }

  /** The Google Form link the student is sent to, with their details filled in. */
  function prefillUrl(klass, deviceUUID, fullName) {
    const params = new URLSearchParams({ usp: "pp_url" });
    params.set("entry." + klass.entryUuid, deviceUUID);
    params.set("entry." + klass.entryName, fullName);
    if (klass.entryCid) params.set("entry." + klass.entryCid, klass.courseCid);
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

  global.MattendEnroll = {
    looksLikeEnrollment, parse, prefillUrl,
    registered, remember, isRegistered, fromLocation,
    STORE_KEY,
  };
})(window);
