# CrateProbe - Frontend

A modern Vue 3 frontend application for CrateProbe, providing real-time monitoring of automated Rust crate testing with Cargo RAPX.

## Features

- **Dashboard View**: Real-time overview of task statistics and system resources
- **Task Management**: Create, view, and manage testing tasks
- **Real-time Updates**: WebSocket integration for live task status updates
- **Log Viewer**: View stdout, stderr, and Miri reports with syntax highlighting
- **Modern UI**: Bento Grid layout with Tailwind CSS for a clean, modern aesthetic
- **Responsive Design**: Works on desktop, tablet, and mobile devices

## Tech Stack

- **Vue 3** - Progressive JavaScript framework (Composition API)
- **Vite** - Next-generation frontend build tool
- **Vue Router** - Official routing library
- **Tailwind CSS v4** - Utility-first CSS framework
- **Axios** - Promise-based HTTP client
- **WebSocket** - Real-time communication with backend

## Prerequisites

- Node.js 18+ and npm
- Backend API running on `http://localhost:8000`

## Installation

1. Install dependencies:
```bash
npm install
```

## Development

Start the development server:
```bash
npm run dev
```

The application will be available at `http://localhost:5173`

### Development Features

- Hot Module Replacement (HMR)
- Proxy to backend API (`/api` and `/ws` routes)
- Fast refresh for Vue components

## Building for Production

Build the application:
```bash
npm run build
```

The built files will be in the `dist/` directory.

Preview the production build:
```bash
npm run preview
```

## Project Structure

```
frontend/
├── src/
│   ├── main.js                 # Application entry point
│   ├── App.vue                 # Root component with navigation
│   ├── style.css               # Global styles and Tailwind imports
│   ├── router/
│   │   └── index.js            # Vue Router configuration
│   ├── views/                  # Page components
│   │   ├── Dashboard.vue       # Dashboard with stats and system monitor
│   │   ├── TaskList.vue        # Task list with filtering and sorting
│   │   ├── TaskNew.vue         # Create new task form
│   │   └── TaskDetail.vue      # Task details with log viewer
│   ├── components/             # Reusable components
│   │   ├── StatCard.vue        # Statistics card component
│   │   ├── SystemMonitor.vue   # System resource monitor
│   │   └── LogViewer.vue       # Log viewer with tabs
│   └── services/               # Service layer
│       ├── api.js              # REST API client
│       └── websocket.js        # WebSocket connection manager
├── public/                     # Static assets
├── index.html                  # HTML template
├── vite.config.js              # Vite configuration
├── tailwind.config.js          # Tailwind CSS configuration
├── postcss.config.js           # PostCSS configuration
└── package.json                # Dependencies and scripts
```

## Routes

- `/` - Redirects to dashboard
- `/dashboard` - Main dashboard view
- `/tasks` - List all tasks
- `/tasks/new` - Create new task
- `/tasks/:id` - View task details

## API Integration

The frontend communicates with the backend via:

1. **REST API** (via Axios):
   - `POST /api/tasks` - Create task
   - `GET /api/tasks` - List tasks
   - `GET /api/tasks/:id` - Get task details
   - `DELETE /api/tasks/:id` - Cancel task
   - `GET /api/dashboard` - Get dashboard stats
   - `GET /api/system/stats` - Get system resources
   - `GET /api/tasks/:id/logs/:type` - Get logs

2. **WebSocket** (for real-time updates):
   - Task status changes
   - System resource updates
   - Task completion notifications

## Configuration

### Backend API URL

The backend API URL is configured in `vite.config.js`:

```javascript
server: {
  proxy: {
    '/api': {
      target: 'http://localhost:8000',
      changeOrigin: true,
    },
    '/ws': {
      target: 'ws://localhost:8000',
      ws: true,
    }
  }
}
```

To change the backend URL, modify the `target` values.

## Styling

The application uses Tailwind CSS v4 with custom utility classes defined in `src/style.css`:

- **Bento Grid**: Modern grid layout system
- **Status Badges**: Color-coded task status indicators
- **Log Viewer**: Monospace code block styling
- **Loading Spinner**: Animated loading indicator

## Browser Support

- Chrome/Edge (latest)
- Firefox (latest)
- Safari (latest)

## Troubleshooting

### Backend Connection Issues

If the frontend cannot connect to the backend:

1. Ensure the backend is running on `http://localhost:8000`
2. Check the proxy configuration in `vite.config.js`
3. Check browser console for CORS errors

### WebSocket Connection Issues

If WebSocket updates are not working:

1. Check that the backend WebSocket endpoint is accessible
2. Look for connection errors in the browser console
3. Verify the WebSocket URL in the browser DevTools Network tab

### Build Failures

If the build fails:

1. Clear `node_modules` and reinstall: `rm -rf node_modules && npm install`
2. Clear Vite cache: `rm -rf node_modules/.vite`
3. Check for syntax errors in Vue components

## Development Tips

- Use Vue DevTools browser extension for debugging
- Check the Network tab for API request/response details
- Use the WebSocket tab in DevTools to monitor real-time messages
- Enable HMR for faster development (enabled by default)

## License

Part of the CrateProbe project.
