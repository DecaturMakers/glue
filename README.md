# Decatur Makers Glue Server

This is a simple web server responsible for "gluing together" parts of the Decatur Makers digital infrastructure. It currently handles:

- RFID authentication at the front door of the makerspace
    - Polls [NeonCRM](https://www.neoncrm.com) for updates 
    - Logs fob scans to a Google Sheet
- Inviting new members to complete a background check with [Checkr](https://checkr.com)

The service is dockerized and deployed to Google Cloud via Cloud Run.

## Building and deploying

The following environment variables need to be set in Google Cloud. See also `example.env`:

- `NEON_PASSWORD`: the password used by NeonCRM when calling the "glue" server via a webhook. The username should be "neoncrm". This password can be set here: [https://decaturmakers.app.neoncrm.com/np/admin/systemsetting/webhook/webHookList.jsp](https://decaturmakers.app.neoncrm.com/np/admin/systemsetting/webhook/webHookList.jsp).
- `NEON_API_KEY`: documented here: [https://developer.neoncrm.com/api/getting-started/api-keys/](https://developer.neoncrm.com/api/getting-started/api-keys/)
- `RFID_TOKENS`: each RFID Raspberry Pi client should be configured with a unique token used to authenticate to this server. This environment variable should contain a space-delimited list of all authorized tokens.
- `RFID_SHEET_URL`: the "sharing" URL of the Google Sheet for logging RFID scans
- `CHECKR_API_KEY`: configured here: [https://dashboard.checkr.com/account/developer_settings](https://dashboard.checkr.com/account/developer_settings)
- `CHECKR_PACKAGE`: the slug of the "package" used for background checks, e.g. `driver_pro` or `pro_criminal`.

You'll also need a `rfid-sheet-service-account.json` for the service account that has access to the "Space Access Reports" Google sheet, currently rfid-260@glue-317617.iam.gserviceaccount.com. Ask evan@decaturmakers.org for this file, or create a new one by going to the "glue" project on Google Cloud -> IAM & Admin -> Service Accounts -> rfid-260@glue-317617.iam.gserviceaccount.com -> Keys -> Add Key -> Create New Key, select JSON, and hit Create.

## To build and deploy a new version:

The environment variables should already be set in Google Cloud, but the `rfid-sheet-service-account.json` needs to be available while building the container. See above if you don't have this file.

```
docker build -t gcr.io/$GCLOUD_PROJECT_ID/glue .
docker push gcr.io/$GCLOUD_PROJECT_ID/glue
gcloud run deploy glue --image gcr.io/$GCLOUD_PROJECT_ID/glue:latest --region=us-east1 --project=$GCLOUD_PROJECT_ID
```
