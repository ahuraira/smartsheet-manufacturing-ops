# API Playground

> **Interactive API Testing Environment** | **Powered by Swagger UI**

---

## 🎮 Try It Out

Open the interactive playground: **[Launch API Playground](./index.html)**

or browse locally after cloning:
```bash
cd docs/api-playground
python3 -m http.server 8000
# Visit: http://localhost:8000
```

---

## Features

✨ **Interactive Testing**
- Try API calls directly from your browser
- See real request/response examples
- Test authentication and parameters

📖 **Complete Documentation**
- All 6 endpoints documented
- Request/response schemas with examples
- Error codes and edge cases

🔧 **Developer-Friendly**
- Copy curl commands
- Generate client code (multiple languages)
- Download OpenAPI spec

---

## Quick Start

1. **Open Playground:** [index.html](./index.html)
2. **Click "Authorize"** and enter your Azure Function key
3. **Select an endpoint** (e.g., Tag Ingestion)
4. **Click "Try it out"**
5. **Modify the example** request
6. **Execute** and see the response

---

## Available Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/tags/ingest` | POST | Upload tag sheets |
| `/api/lpo/ingest` | POST | Create LPO records |
| `/api/nesting/parse` | POST | Parse nesting files |
| `/api/map/lookup` | POST | Material code lookup |
| `/api/production/schedule` | POST | Schedule production |
| `/api/events/process-row` | POST | Process webhook events |

---

## Authentication

The playground uses **Azure Functions authentication**.

### Setup:
1. Get your function key from Azure Portal
2. In the playground, click **"Authorize"**
3. Enter: `code: YOUR_FUNCTION_KEY`
4. Click "Authorize"

All subsequent requests will include your auth token.

---

## Using the OpenAPI Spec

### Download
- Download: [openapi.yaml](./openapi.yaml)
- Format: OpenAPI 3.0.3
- Use with: Postman, Insomnia, API clients

### Generate Client SDKs

```bash
# Install OpenAPI Generator
npm install -g @openapitools/openapi-generator-cli

# Generate Python client
openapi-generator-cli generate \
  -i openapi.yaml \
  -g python \
  -o ./sdk/python

# Generate TypeScript client
openapi-generator-cli generate \
  -i openapi.yaml \
  -g typescript-fetch \
  -o ./sdk/typescript
```

---

## Deployment

### Local Testing
```bash
cd docs/api-playground
python3 -m http.server 8000
```

### GitHub Pages
This playground works on GitHub Pages automatically:
- URL: `https://your-org.github.io/repo/docs/api-playground/`
- No build required (static HTML + CDN libraries)

### Azure Static Web Apps
```bash
# Deploy to Azure
az staticwebapp create \
  --name ducts-api-docs \
  --source docs/api-playground \
  --location eastus2
```

---

## Customization

### Update Server URL
Edit `openapi.yaml`:
```yaml
servers:
  - url: https://YOUR-FUNCTION-APP.azurewebsites.net
    description: Production
```

### Add New Endpoint
1. Add to `openapi.yaml` under `paths:`
2. Define request/response schemas
3. Reload playground (no rebuild needed)

---

## Troubleshooting

### CORS Errors
If you see CORS errors when testing:
- Ensure your Azure Function allows `http://localhost:8000` origin
- Or use the deployed playground URL from GitHub Pages

### 401 Unauthorized
- Verify function key is correct
- Check key hasn't expired
- Ensure "Authorize" button was clicked

### Spec Not Loading
- Verify `openapi.yaml` is valid: https://editor.swagger.io/
- Check browser console for errors
- Ensure serving from HTTP server (not `file://`)

---

## Related Documentation

- [API Reference](../reference/api/index.md) - Full API docs
- [First API Integration Tutorial](../tutorials/first_api_integration.md) - Beginner guide
- [Authentication Guide](../howto/authentication.md) - Auth details

---

## Feedback

Found an issue with the playground or spec?
- Open an issue with label `documentation`
- Suggest improvements via PR
