# Security policy

RiceGod is currently a portfolio and local-development project. It has not yet
completed the authentication, authorization, rate-limiting, and privacy work
required for exposure to untrusted public traffic.

## Secrets

- Never commit `.env` or provider credentials.
- Copy `.env.example` to `.env` and use separate development credentials.
- Rotate a credential immediately if it has been pushed to a remote repository,
  included in a build artifact, or printed in a public log.
- Production deployments should use a managed secret store instead of an env
  file checked into the image.

## Supported use

Run the service on a trusted local network until authentication is implemented.
The `user_id` request field is a data-partition key, not proof of identity.

## Reporting

Do not open a public issue containing credentials, personal chat content, or a
working exploit. Contact the repository owner privately and include the affected
version, reproduction steps, impact, and a suggested mitigation when possible.
