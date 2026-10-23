# Website chat widget

Plain JavaScript, no build, no cookies. Served from the same CloudFront distribution as the
inbox (`/widget/widget.js`). Replies are inserted with `textContent`, never `innerHTML`, so a
reply can never become markup on the client's page.

```html
<script src="https://<cloudfront-domain>/widget/widget.js"
        data-key="fdk_live_..." data-api="https://<api-id>.execute-api.af-south-1.amazonaws.com"
        data-ws="wss://<ws-id>.execute-api.af-south-1.amazonaws.com/v1" defer></script>
```

Create a key with `python -m frontdesk.admin create-widget-key --tenant <id> --origin https://www.client.co.za --dry-run`
(then `--write`). The key only works from the origins it was created for.
