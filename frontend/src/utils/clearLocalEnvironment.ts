/**
 * Wipe all OpenMail client data in this browser origin.
 * Used when the user forgot vault password/recovery, or wants a clean slate.
 * Does NOT call the server (cloud sealed rows under old device id remain until deleted separately).
 */

const PREFIX = 'openmail.'

function removeMatching(storage: Storage) {
  const keys: string[] = []
  for (let i = 0; i < storage.length; i++) {
    const k = storage.key(i)
    if (k && (k === PREFIX.slice(0, -1) || k.startsWith(PREFIX))) {
      keys.push(k)
    }
  }
  for (const k of keys) {
    try {
      storage.removeItem(k)
    } catch {
      /* ignore */
    }
  }
}

/** Remove every openmail.* key from localStorage + sessionStorage. */
export function clearAllOpenMailStorage(): number {
  let n = 0
  try {
    const before = localStorage.length
    removeMatching(localStorage)
    n += Math.max(0, before - localStorage.length)
  } catch {
    /* private mode etc. */
  }
  try {
    removeMatching(sessionStorage)
  } catch {
    /* ignore */
  }
  return n
}

/**
 * Hard reset: clear storage and reload so all Pinia modules start empty.
 * Call after optional server-side cleanups if any.
 */
export function factoryResetAndReload(): void {
  clearAllOpenMailStorage()
  try {
    // Belt-and-suspenders: scan again in case of mid-loop inserts
    clearAllOpenMailStorage()
  } catch {
    /* ignore */
  }
  // Full navigation reset (clears memory stores)
  window.location.assign(window.location.pathname || '/')
}
