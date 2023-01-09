# Decatur Makers Glue Server

This is a simple web server responsible for "gluing together" parts of the Decatur Makers digital infrastructure. It currently handles:

- RFID authentication at the front door of the makerspace
    - Polls [NeonCRM](https://www.neoncrm.com) for updates 
    - Logs fob scans to a Google Sheet
- Inviting new members to complete a background check with [Checkr](https://checkr.com)

The service is dockerized and deployed to Google Cloud via Cloud Run.

## Building and deploying

Commits to the master branch should trigger an automatic deployment to Cloud Run via GitHub Actions.

The following environment variables need to be set in Google Cloud. See also `example.env`:

- `NEON_PASSWORD`: the password used by NeonCRM when calling the "glue" server via a webhook. The username should be "neoncrm". This password can be set here: [https://decaturmakers.app.neoncrm.com/np/admin/systemsetting/webhook/webHookList.jsp](https://decaturmakers.app.neoncrm.com/np/admin/systemsetting/webhook/webHookList.jsp).
- `NEON_API_KEY`: documented here: [https://developer.neoncrm.com/api/getting-started/api-keys/](https://developer.neoncrm.com/api/getting-started/api-keys/)
- `RFID_TOKENS`: each RFID Raspberry Pi client should be configured with a unique token used to authenticate to this server. This environment variable should contain a space-delimited list of all authorized tokens.
- `RFID_SHEET_URL`: the "sharing" URL of the Google Sheet for logging RFID scans
- `CHECKR_API_KEY`: configured here: [https://dashboard.checkr.com/account/developer_settings](https://dashboard.checkr.com/account/developer_settings)
- `CHECKR_PACKAGE`: the slug of the "package" used for background checks, e.g. `driver_pro` or `pro_criminal`.

## To run manually:

1. Create `rfid-sheet-service-account.json` at the root of the repository. This file allows the glue server access to the "Space Access Reports" Google sheet as the service account `rfid-260@glue-317617.iam.gserviceaccount.com`. The content of this file should be the JSON data stored in the `RFID_SHEET_SERVICE_ACCOUNT_JSON` secret on this repository: https://github.com/DecaturMakers/glue/settings/secrets/actions. If the secret is missing, you can get a new JSON file by going to the "glue" project on Google Cloud -> IAM & Admin -> Service Accounts -> rfid-260@glue-317617.iam.gserviceaccount.com -> Keys -> Add Key -> Create New Key, select JSON, and hit Create.

2. Create a file named `.env` at the root of the repository, following the format of `example.env`. Look in Cloud Run for the correct values of the environment variables.

3. ```
   poetry shell
   poetry install
   python3 main.py
   ```
   
### To build and deploy manually:

`rfid-sheet-service-account.json` needs to be available while building the container. See above if you don't have this file.
```
export GCLOUD_PROJECT_ID=glue-317617
docker build -t gcr.io/$GCLOUD_PROJECT_ID/glue .
docker push gcr.io/$GCLOUD_PROJECT_ID/glue
gcloud run deploy glue --image gcr.io/$GCLOUD_PROJECT_ID/glue:latest --region=us-east1 --project=$GCLOUD_PROJECT_ID
```
