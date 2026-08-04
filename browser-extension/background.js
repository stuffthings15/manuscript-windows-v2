const NATIVE_HOST = "com.manuscript.polish";

chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
  if (!request || request.type !== "analyze") {
    sendResponse({ id: "", ok: false, error: "invalid_request" });
    return false;
  }
  chrome.runtime.sendNativeMessage(NATIVE_HOST, request, (response) => {
    if (chrome.runtime.lastError) {
      sendResponse({
        id: request.id || "",
        ok: false,
        error: "native_host_unavailable"
      });
      return;
    }
    sendResponse(response || {
      id: request.id || "",
      ok: false,
      error: "invalid_native_response"
    });
  });
  return true;
});
