// One-shot values passed in the URL fragment. Fragments are never sent to the
// server, so tokens there do not end up in access logs or Referer headers.

/** Invite token from `#invite=<token>`. */
export function readInviteToken(): string | null {
  const match = window.location.hash.match(/^#invite=([\w-]+)$/)
  return match ? match[1] : null
}

/** Result of the Shopee OAuth redirect from `#shopee=connected|error:<code>`. */
export function readShopeeCallback(): string | null {
  const match = window.location.hash.match(/^#shopee=([\w:]+)$/)
  return match ? match[1] : null
}
