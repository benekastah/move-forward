# vcard

An address book, read from vCard files, so the people who matter rank first wherever they
show up: their mail, their PRs, a ticket they reported. Enabled by a `[vcard]` section. It
reads the files and writes nothing.

## Config

```toml
[vcard]
paths = ["~/.contacts"]
group_importance = {Family = 2, Favorites = 1}
group_types = {Clients = "customer"}
```

- `paths` (required): vCard files (`.vcf`, one or many contacts each), or directories searched
  for them, below them too: an export from Contacts, Google Contacts or Outlook, or the
  directory [vdirsyncer](https://vdirsyncer.pimutils.org/) keeps in sync with a CardDAV
  server (iCloud, Fastmail, Nextcloud) or with Google Contacts (its `google_contacts`
  storage). A path that isn't there fails the refresh.
- `group_importance` (`{}`): how much the people in a group matter to me (0 normal, 1 high, 2
  critical), by its name, ignoring case; the highest of their groups' counts.
- `group_types` (`{}`): what the people in a group are to me, a contact type (see the README's
  People), by its name, ignoring case; the first listed of their groups'. A type starts with a
  letter, then up to 39 letters, digits, spaces, `-` or `_`; one that doesn't is ignored.
- `refresh_seconds` (3600): how often it reads the files anyway. It also looks every minute
  for a file added, removed or written (by their times and sizes, without reading them), and
  reads them at once if there is one.

A group is a card's category (`CATEGORIES`), or a group's own card naming its members: vCard
4's `KIND:group` with `MEMBER`s (by `UID` or `mailto:` address), or Apple's
`X-ADDRESSBOOKSERVER-KIND` (what iCloud syncs). Only a person's card (no `KIND`, or
`KIND:individual`) gives contacts: an organisation's or a place's doesn't. It reads vCard 4, 3 and 2.1 (quoted-printable
values too); of a card, only its name, addresses, categories and `UID`.

## Publishes

A `person` for each address in the address book, `vcard/<address>`, titled by their name, with
the `importance` and `contact_type` their groups give and the `groups` they're in; see
[entities.md](../entities.md). Core joins them with the same person in other tools by email
(mail's senders, github's committers), so their items rank and are badged by it. A card with
several addresses gives a person for each; an address on several cards takes the name of the
first that has one (else it's named by itself) and every card's groups. A type I set on someone
(Set type) beats their groups'.

## Panels

None: what it knows shows on other plugins' items about the same people.
