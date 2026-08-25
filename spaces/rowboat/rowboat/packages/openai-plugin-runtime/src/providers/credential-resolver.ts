const CREDENTIAL_REFERENCE = /^[A-Za-z0-9][A-Za-z0-9._:/-]*$/;
const PROJECT_ID = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

const secretValues = new WeakMap<SecretValue, string>();

export type CredentialReference = Readonly<
  | { readonly kind: "bearer"; readonly reference: string }
  | { readonly kind: "oauth"; readonly reference: string }
  | { readonly kind: "environment"; readonly reference: string }
>;

/** A deliberately non-enumerable credential wrapper. */
export class SecretValue {
  private constructor() {
    Object.freeze(this);
  }

  static create(value: string): SecretValue {
    if (value.length === 0 || value.includes("\0")) {
      throw new Error("credential_invalid");
    }
    const secret = new SecretValue();
    secretValues.set(secret, value);
    return secret;
  }

  toString(): string {
    return "[REDACTED]";
  }

  toJSON(): string {
    return "[REDACTED]";
  }
}

export function createSecretValue(value: string): SecretValue {
  return SecretValue.create(value);
}

export function revealSecretValue(secret: SecretValue): string {
  const value = secretValues.get(secret);
  if (value === undefined) throw new Error("credential_invalid");
  return value;
}

export function assertCredentialRequest(
  reference: CredentialReference,
  projectId: string,
): void {
  if (!PROJECT_ID.test(projectId) || !CREDENTIAL_REFERENCE.test(reference.reference)) {
    throw new Error("credential_invalid");
  }
}

export interface CredentialResolver {
  resolve(reference: CredentialReference, projectId: string): Promise<SecretValue>;
}
