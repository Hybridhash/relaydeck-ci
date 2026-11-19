# Accessibility

RelayDeck targets [WCAG 2.2 Level AA](https://www.w3.org/TR/WCAG22/).
This is a development target, not a claim of complete conformance.

The design system specifies 48 px touch targets, theme tokens and visible
interaction states. The web shell permits browser zoom and declares English as
its language. These foundations do not establish that every screen or terminal
interaction is accessible.

## Known limits

The custom terminal, text selection and mobile keyboard controls need
screen-reader and keyboard testing on each supported browser and device.
System motion preferences and layouts at the largest text sizes still need
end-to-end verification. Headless UI tests and token contrast tests do not
replace those checks. Native applications are not yet released.

The interface is English-only. Translation, complete device-locale formatting
and right-to-left presentation are not supported in the 0.3 series.

## Report a barrier

Open a repository issue describing the screen, action, application version,
device, browser, assistive technology and expected result. Use synthetic text;
do not include terminal transcripts, credentials or private project details.
Use the [private security route](../SECURITY.md#report-a-vulnerability-privately)
for a security-sensitive report.

See [contributing](../CONTRIBUTING.md#design-system-admission) for shared-control
requirements and the [language decision](decisions/english-only.md).
