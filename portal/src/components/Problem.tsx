import { ApiError, StepUpRequired } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { Button, Notice } from "./ui";

/** An API refusal, in words; a step-up refusal offers the way through. */
export function Problem({ error }: { error: unknown }) {
  const auth = useAuth();
  if (error instanceof StepUpRequired) {
    if (error.code === "mfa_required") {
      return (
        <Notice tone="error">
          <p>This needs two-factor authentication. Set up an authenticator app, then confirm it is you.</p>
          <Button onClick={() => void auth.setUpAuthenticator()}>Set up authenticator</Button>
        </Notice>
      );
    }
    return (
      <Notice tone="error">
        <p>For your security, confirm it is you with your authenticator code.</p>
        <Button onClick={() => void auth.stepUp()}>Confirm it is you</Button>
      </Notice>
    );
  }
  const message =
    error instanceof ApiError ? describe(error) : error instanceof Error ? error.message : "Something went wrong.";
  return <Notice tone="error">{message}</Notice>;
}

const MESSAGES: Record<string, string> = {
  refused: "We cannot offer our services to you.",
  unknown_code: "This discount code is not valid.",
  blocked: "Your account is blocked. Please contact support.",
  screening: "We cannot complete this request. Please contact support.",
  unknown_product: "This product is not available.",
  maintenance: "We are doing some maintenance. Purchases and registrations are paused for a short while.",
  registration_closed: "Registration is closed for now.",
};

function describe(error: ApiError): string {
  if (error.code === "refused" && error.message.includes("age:")) {
    return "You must be at least 18 years old to register.";
  }
  if (error.code !== null && error.code in MESSAGES) return MESSAGES[error.code] ?? error.message;
  if (error.status === 409 && error.message === "already registered") return "You are already registered.";
  return error.message;
}
