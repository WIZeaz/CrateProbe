const ADMIN_TOKEN_KEY = 'admin_token'

export function setAdminToken(token) {
  sessionStorage.setItem(ADMIN_TOKEN_KEY, token)
}

export function getAdminToken() {
  return sessionStorage.getItem(ADMIN_TOKEN_KEY)
}

export function clearAdminToken() {
  sessionStorage.removeItem(ADMIN_TOKEN_KEY)
}
