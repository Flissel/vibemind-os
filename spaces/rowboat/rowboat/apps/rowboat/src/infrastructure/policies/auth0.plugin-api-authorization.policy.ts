import { createRemoteJWKSet, jwtVerify, type JWTVerifyGetKey } from "jose";
import { types as utilTypes } from "node:util";
import { NextRequest } from "next/server";
import { USE_AUTH } from "@/app/lib/feature_flags";
import type { IApiKeysRepository } from "@/src/application/repositories/api-keys.repository.interface";
import type { IProjectMembersRepository } from "@/src/application/repositories/project-members.repository.interface";
import type { IUsersRepository } from "@/src/application/repositories/users.repository.interface";
import type { IPluginApiAuthorizationPolicy, PluginApiIdentity } from "@/src/application/policies/plugin-api-authorization.policy";

const SUBJECT = /^[A-Za-z0-9|._:@/-]{1,256}$/;
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const MAX_TOKEN = 16_384;

export interface PluginUserSessionProvider { getUserId(): Promise<string | null>; }
export interface PluginProjectApiKeyVerifier { verify(token: string): Promise<string | null>; }
export interface PluginUserTokenVerifier { verify(token: string): Promise<string | null>; }

export async function verifyAuth0UserToken(token: string, options: {
  readonly issuer: string;
  readonly audience: string;
  readonly key: JWTVerifyGetKey;
  readonly lookupUserId: (subject: string) => Promise<string | null>;
}): Promise<string | null> {
  try {
    const verified = await jwtVerify(token, options.key, {
      issuer: options.issuer,
      audience: options.audience,
      requiredClaims: ["exp", "sub"],
    });
    if (
      typeof verified.payload.sub !== "string" || !SUBJECT.test(verified.payload.sub)
      || typeof verified.payload.exp !== "number" || !Number.isFinite(verified.payload.exp)
    ) return null;
    return await options.lookupUserId(verified.payload.sub);
  } catch { return null; }
}

export class Auth0PluginUserSessionProvider implements PluginUserSessionProvider {
  constructor(private readonly dependencies: { readonly usersRepository: IUsersRepository }) {}
  async getUserId(): Promise<string | null> {
    if (!USE_AUTH) return "guest_user";
    const { auth0 } = await import("@/app/lib/auth0");
    const session = await auth0.getSession();
    const subject = session?.user?.sub;
    if (typeof subject !== "string" || !SUBJECT.test(subject)) return null;
    return (await this.dependencies.usersRepository.fetchByAuth0Id(subject))?.id ?? null;
  }
}

export class ExistingProjectApiKeyVerifier implements PluginProjectApiKeyVerifier {
  constructor(private readonly dependencies: { readonly apiKeysRepository: IApiKeysRepository }) {}
  verify(token: string): Promise<string | null> { return this.dependencies.apiKeysRepository.findProjectIdAndConsumeKey(token); }
}

export class JoseAuth0UserTokenVerifier implements PluginUserTokenVerifier {
  private readonly issuer = process.env.AUTH0_ISSUER_BASE_URL;
  private readonly audience = process.env.AUTH0_AUDIENCE;
  constructor(private readonly dependencies: { readonly usersRepository: IUsersRepository }) {}
  async verify(token: string): Promise<string | null> {
    if (typeof this.issuer !== "string" || typeof this.audience !== "string") return null;
    try {
      const issuer = this.issuer.endsWith("/") ? this.issuer : `${this.issuer}/`;
      return await verifyAuth0UserToken(token, {
        issuer,
        audience: this.audience,
        key: createRemoteJWKSet(new URL(".well-known/jwks.json", issuer)),
        lookupUserId: async (subject) => (await this.dependencies.usersRepository.fetchByAuth0Id(subject))?.id ?? null,
      });
    } catch { return null; }
  }
}

const REQUEST_HEADERS_GETTER = Object.getOwnPropertyDescriptor(Request.prototype, "headers")?.get;
const HEADERS_GET = Headers.prototype.get;

function authorizationHeader(request: Request): string | null {
  if (utilTypes.isProxy(request) || REQUEST_HEADERS_GETTER === undefined) throw new Error("request_invalid");
  const prototype = Object.getPrototypeOf(request);
  if (prototype !== Request.prototype && prototype !== NextRequest.prototype) throw new Error("request_invalid");
  if (Object.getOwnPropertyDescriptor(request, "headers") !== undefined) throw new Error("request_invalid");
  let headers: Headers;
  try { headers = REQUEST_HEADERS_GETTER.call(request) as Headers; } catch { throw new Error("request_invalid"); }
  if (
    utilTypes.isProxy(headers) || Object.getPrototypeOf(headers) !== Headers.prototype
    || Object.getOwnPropertyDescriptor(headers, "get") !== undefined
  ) throw new Error("request_invalid");
  let value: string | null;
  try { value = HEADERS_GET.call(headers, "authorization"); } catch { throw new Error("request_invalid"); }
  if (value === null) return null;
  if (value.length > MAX_TOKEN + 7 || value.includes(",") || value.includes("\0")) throw new Error("authorization_invalid");
  return value;
}

export class Auth0PluginApiAuthorizationPolicy implements IPluginApiAuthorizationPolicy {
  constructor(private readonly dependencies: {
    readonly pluginUserSessionProvider: PluginUserSessionProvider;
    readonly pluginProjectApiKeyVerifier: PluginProjectApiKeyVerifier;
    readonly pluginUserTokenVerifier: PluginUserTokenVerifier;
    readonly projectMembersRepository: IProjectMembersRepository;
    readonly pluginAuthEnabled: boolean;
  }) {}

  async authenticate(request: Request): Promise<PluginApiIdentity> {
    const header = authorizationHeader(request);
    if (header === null) {
      const userId = await this.dependencies.pluginUserSessionProvider.getUserId().catch(() => null);
      if (userId === null || !ID.test(userId)) throw new Error("unauthenticated");
      return Object.freeze({ kind: "user", userId });
    }
    const match = /^Bearer ([\x21-\x7e]+)$/.exec(header);
    if (match === null || match[1]!.length > MAX_TOKEN) throw new Error("authorization_invalid");
    const token = match[1]!;
    const projectId = await this.dependencies.pluginProjectApiKeyVerifier.verify(token).catch(() => null);
    if (projectId !== null) {
      if (!ID.test(projectId)) throw new Error("unauthenticated");
      return Object.freeze({ kind: "project_api_key", projectId });
    }
    if (!this.dependencies.pluginAuthEnabled) throw new Error("unauthenticated");
    const subject = await this.dependencies.pluginUserTokenVerifier.verify(token).catch(() => null);
    if (subject === null || !ID.test(subject)) throw new Error("unauthenticated");
    return Object.freeze({ kind: "user", userId: subject });
  }

  async authorizeProject(identity: PluginApiIdentity, projectId: string): Promise<void> {
    if (identity.kind === "project_api_key") {
      if (identity.projectId !== projectId) throw new Error("forbidden");
      return;
    }
    if (!await this.dependencies.projectMembersRepository.exists(projectId, identity.userId)) throw new Error("forbidden");
  }
}
