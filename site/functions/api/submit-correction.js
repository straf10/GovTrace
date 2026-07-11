const MAX_TEXT_FIELD_LEN = 200;
const MAX_MESSAGE_LEN = 5000;
const RATE_LIMIT_WINDOW_SECONDS = 3600;
const RATE_LIMIT_MAX_REQUESTS = 5;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function jsonResponse(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function isRateLimited(kv, ip) {
  const key = `ratelimit:submit-correction:${ip}`;
  const raw = await kv.get(key);
  const count = raw ? parseInt(raw, 10) : 0;
  if (count >= RATE_LIMIT_MAX_REQUESTS) return true;
  await kv.put(key, String(count + 1), { expirationTtl: RATE_LIMIT_WINDOW_SECONDS });
  return false;
}

export async function onRequestPost({ request, env }) {
  let body;
  try {
    body = await request.json();
  } catch {
    return jsonResponse({ ok: false, error: "invalid_request" }, 400);
  }

  const {
    name = "",
    email = "",
    message = "",
    vat = "",
    entityName = "",
    website = "", // honeypot -- πρέπει να μένει κενό, δεν εμφανίζεται σε ανθρώπους
  } = body ?? {};

  if (typeof website === "string" && website.trim() !== "") {
    // bot: επιστρέφουμε ψευδή επιτυχία χωρίς να στείλουμε τίποτα
    return jsonResponse({ ok: true });
  }

  if (typeof message !== "string" || message.trim().length === 0 || message.length > MAX_MESSAGE_LEN) {
    return jsonResponse({ ok: false, error: "invalid_message" }, 400);
  }
  if (typeof email !== "string" || email.length > MAX_TEXT_FIELD_LEN || !EMAIL_RE.test(email)) {
    return jsonResponse({ ok: false, error: "invalid_email" }, 400);
  }
  if (typeof name !== "string" || name.length > MAX_TEXT_FIELD_LEN) {
    return jsonResponse({ ok: false, error: "invalid_name" }, 400);
  }
  if (typeof vat !== "string" || vat.length > 20 || typeof entityName !== "string" || entityName.length > MAX_TEXT_FIELD_LEN) {
    return jsonResponse({ ok: false, error: "invalid_context" }, 400);
  }

  if (env.RATE_LIMIT_KV) {
    const ip = request.headers.get("CF-Connecting-IP") || "unknown";
    if (await isRateLimited(env.RATE_LIMIT_KV, ip)) {
      return jsonResponse({ ok: false, error: "rate_limited" }, 429);
    }
  }

  if (!env.RESEND_API_KEY) {
    return jsonResponse({ ok: false, error: "server_misconfigured" }, 500);
  }

  const context = vat ? `${entityName || vat} (ΑΦΜ ${vat})` : null;
  const subject = `[ΔΙΟΡΘΩΣΗ/ΑΠΑΝΤΗΣΗ]${context ? ` ${context}` : ""}`;

  const textBody = [
    "Νέο αίτημα από τη φόρμα /diorthoseis/ στο ellada30.pages.dev.",
    "",
    `Όνομα: ${name.trim() || "(δεν δόθηκε)"}`,
    `Email αποστολέα: ${email}`,
    context ? `Φορέας/ΑΦΜ: ${context}` : null,
    "",
    "Μήνυμα:",
    message.trim(),
  ]
    .filter((line) => line !== null)
    .join("\n");

  let resendResponse;
  try {
    resendResponse = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.RESEND_API_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        from: "Ελλάδα 3.0 <onboarding@resend.dev>",
        to: ["ellada30@proton.me"],
        reply_to: email,
        subject,
        text: textBody,
      }),
    });
  } catch {
    return jsonResponse({ ok: false, error: "send_failed" }, 502);
  }

  if (!resendResponse.ok) {
    return jsonResponse({ ok: false, error: "send_failed" }, 502);
  }

  return jsonResponse({ ok: true });
}

export async function onRequestGet() {
  return jsonResponse({ ok: false, error: "method_not_allowed" }, 405);
}
