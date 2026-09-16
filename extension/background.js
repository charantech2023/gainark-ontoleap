/**
 * background.js — Extension Service Worker (Manifest V3)
 * Manages tab session memory cleanup when tabs are closed.
 */

chrome.tabs.onRemoved.addListener((tabId) => {
  if (chrome.storage && chrome.storage.session) {
    const key = `tab_${tabId}`;
    chrome.storage.session.remove(key, () => {
      // Cleaned up tab memory
    });
  }
});
