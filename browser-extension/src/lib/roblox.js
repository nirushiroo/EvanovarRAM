/**
 * Roblox URL parsing and command building for the Evanovar RAM bridge.
 *
 * The desktop application parses commands with Python's shlex, so arguments
 * that may contain spaces, quotes or unusual characters are quoted here.
 */

const PLACE_ID_PATTERNS = [
  /\/games\/(\d+)/i,
  /\/game\/(\d+)/i,
  /\/places\/(\d+)/i,
];

const AUTH_SETTING_KEY = "ram_token";

/** Matches characters that are safe to send as an unquoted shlex argument. */
const SAFE_ARGUMENT = /^[A-Za-z0-9_@./:+=,-]+$/;

/**
 * Quote a single command argument so shlex returns it unchanged.
 * Quotes and backslashes are escaped, everything else is left alone.
 */
export function quoteArg(value) {
  const text = String(value ?? "");
  if (SAFE_ARGUMENT.test(text)) {
    return text;
  }
  return `"${text.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

/**
 * Read the place, server and user information out of a Roblox URL.
 * Returns an empty context for anything that is not a roblox.com page.
 */
export function parseRobloxContext(rawUrl) {
  const context = {
    isRoblox: false,
    placeId: "",
    privateServerLink: "",
    jobId: "",
    userId: "",
    label: "No Roblox page detected.",
  };

  let url;
  try {
    url = new URL(String(rawUrl || ""));
  } catch (error) {
    return context;
  }

  const host = url.hostname.toLowerCase();
  if (!host.endsWith("roblox.com")) {
    return context;
  }

  context.isRoblox = true;
  const path = url.pathname;

  for (const pattern of PLACE_ID_PATTERNS) {
    const match = path.match(pattern);
    if (match) {
      context.placeId = match[1];
      break;
    }
  }

  if (!context.placeId) {
    const queryPlace = url.searchParams.get("placeId") || url.searchParams.get("placeid");
    if (queryPlace && /^\d+$/.test(queryPlace)) {
      context.placeId = queryPlace;
    }
  }

  context.jobId =
    url.searchParams.get("gameInstanceId") || url.searchParams.get("gameId") || "";

  const linkCode = url.searchParams.get("privateServerLinkCode");
  if (linkCode) {
    context.privateServerLink = context.placeId
      ? `https://www.roblox.com/games/${context.placeId}?privateServerLinkCode=${linkCode}`
      : url.href;
  }

  const userMatch = path.match(/\/users\/(\d+)/i);
  if (userMatch) {
    context.userId = userMatch[1];
  }

  context.label = describeContext(context);
  return context;
}

function describeContext(context) {
  if (context.placeId) {
    const parts = [`Place ${context.placeId}`];
    if (context.privateServerLink) {
      parts.push("private server");
    }
    if (context.jobId) {
      parts.push(`job ${context.jobId}`);
    }
    return parts.join(" - ");
  }
  if (context.userId) {
    return `User ${context.userId} - use Join to join their game.`;
  }
  return "Roblox page detected, but no Place ID or user was found.";
}

/**
 * Launch <account> <place_id> [private_server] [job_id]
 * An empty private server is sent as "" so the job ID keeps its position.
 */
export function buildLaunchCommand(account, context, options = {}) {
  if (!account) {
    throw new Error("Select an account first.");
  }
  if (!context || !context.placeId) {
    throw new Error("Open a Roblox game page first, or enter a Place ID.");
  }

  const args = [quoteArg(account), quoteArg(context.placeId)];

  if (options.includeJobId && context.jobId) {
    args.push(context.privateServerLink ? quoteArg(context.privateServerLink) : '""');
    args.push(quoteArg(context.jobId));
  } else if (context.privateServerLink) {
    args.push(quoteArg(context.privateServerLink));
  }

  return `Launch ${args.join(" ")}`;
}

/**
 * MultiLaunch <place_id> <private_server> <account> [account ...]
 * The private server is sent as an empty string when there is none, so the
 * account names keep their positions. Job IDs are not part of a bulk launch.
 */
export function buildMultiLaunchCommand(accounts, context) {
  const names = (accounts || []).filter(Boolean);
  if (names.length === 0) {
    throw new Error("Check at least one account first.");
  }
  if (!context || !context.placeId) {
    throw new Error("Open a Roblox game page first, or enter a Place ID.");
  }

  const args = [quoteArg(context.placeId)];
  args.push(context.privateServerLink ? quoteArg(context.privateServerLink) : '\"\"');
  for (const name of names) {
    args.push(quoteArg(name));
  }

  return `MultiLaunch ${args.join(" ")}`;
}

/** JoinUser <account> <target_username_or_user_id> */
export function buildJoinUserCommand(account, target) {
  if (!account) {
    throw new Error("Select an account first.");
  }
  const value = String(target || "").trim();
  if (!value) {
    throw new Error("Enter a username or user ID to join.");
  }
  return `JoinUser ${quoteArg(account)} ${quoteArg(value)}`;
}

/**
 * Add <cookie>
 * The application reads everything after "Add" verbatim, so the cookie is
 * never quoted and must not contain whitespace.
 */
export function buildAddCommand(cookie) {
  const value = String(cookie || "").trim();
  if (!value) {
    throw new Error("No Roblox session was found in this browser.");
  }
  if (/\s/.test(value)) {
    throw new Error("The Roblox session cookie has an unexpected format.");
  }
  return `Add ${value}`;
}

export { AUTH_SETTING_KEY };
