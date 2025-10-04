import streamlit as st
import pandas as pd
import time
from pathlib import Path
import plotly.express as px
from collector import SerialCollector, SerialReadError
from sender import send_mmwave_config   # <--- use shared sender
import logging

# -----------------------------
# Streamlit App Setup
# -----------------------------
st.set_page_config(page_title="mmWave Visualizer", layout="wide")

# --- Initialize Logger ---
logger = logging.getLogger("mmwave_collector")
logger.setLevel(logging.INFO)
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))
    logger.addHandler(ch)

st.title("🌐 mmWave Demo Visualizer")

# --- SESSION STATE INITIALIZATION ---
if "collector" not in st.session_state: 
    st.session_state.collector = None
if "df" not in st.session_state:
    st.session_state.df = pd.DataFrame(columns=["x", "y", "z", "velocity", "rng", "timestamp", "state"])
if "log_messages" not in st.session_state: 
    st.session_state.log_messages = []
if "is_running" not in st.session_state: 
    st.session_state.is_running = False

def log(msg: str):
    st.session_state.log_messages.append(f"{time.strftime('%H:%M:%S')} - {msg}")
    st.session_state.log_messages = st.session_state.log_messages[-200:]

# --- SIDEBAR: CONTROLS ---
with st.sidebar:
    st.header("Connection Settings")
    is_disabled = st.session_state.is_running
    
    st.subheader("Data Port")
    data_com_port = st.text_input("Data Serial Port (e.g., COM4)", value="COM4", disabled=is_disabled)
    data_baud = st.selectbox("Data Baud Rate", [921600, 115200], index=0, disabled=is_disabled)

    st.subheader("Configuration Port")
    config_com_port = st.text_input("Config Serial Port (e.g., COM5, or same as Data)", value="COM5", disabled=is_disabled)
    config_baud = st.selectbox("Config Baud Rate", [115200, 921600], index=0, disabled=is_disabled)

    st.subheader("Config File")
    config_file_path = st.text_input("Config File Path", value="mmwave_config.cfg", disabled=is_disabled)

    st.markdown("---")
    movement_thresh = st.slider("Movement Threshold (m/s)", 0.0, 5.0, 0.2, 0.01)

    st.header("Data & Plotting")
    max_points = st.number_input("Max Points in Memory", min_value=10, max_value=100000, value=5000, step=100)
    poll_interval = st.slider("UI Poll Interval (s)", 0.1, 2.0, 0.2, 0.05)

    st.header("View Options")
    view_mode = st.radio("Plot Projection", ["3D Scatter", "X vs Y", "Y vs Z", "Z vs X"])
    point_size = st.slider("Point Size", 2, 30, 8)

    # Fixed axis ranges for radar plotting
    st.subheader("Radar Axis Ranges")
    x_min, x_max = st.slider("X Range (left-right)", -10.0, 10.0, (-5.0, 5.0))
    y_min, y_max = st.slider("Y Range (forward distance)", 0.0, 20.0, (0.0, 10.0))
    z_min, z_max = st.slider("Z Range (height)", -5.0, 5.0, (-2.0, 2.0))

    st.header("CSV Logging")
    csv_log = st.checkbox("Log new points to CSV", value=False)
    csv_path = st.text_input("CSV File Path", value="moving_objects.csv", disabled=not csv_log)

    col1, col2 = st.columns(2)
    if col1.button("Start Collector", disabled=st.session_state.is_running, type="primary"):
        try:
            success = send_mmwave_config("mmwave_config.cfg", config_port=config_com_port, config_baud=config_baud)
            if not success:
                st.error("⚠️ Failed to send config file to radar.")
                st.stop()

            if data_com_port == config_com_port:
                log(f"Same port used for config & data ({data_com_port}). Switching baud to {data_baud} for data stream.")
                sc = SerialCollector(data_port=data_com_port, data_baud=data_baud)
            else:
                sc = SerialCollector(data_port=data_com_port, data_baud=data_baud)

            sc.start()

            st.session_state.collector = sc
            st.session_state.is_running = True
            log(f"Collector started. Data on {data_com_port} @ {data_baud}, config sent via {config_com_port} @ {config_baud}")
            st.rerun()

        except Exception as e:
            st.error(f"Unexpected error: {e}")
            log(f"Unexpected error during start: {e}")
            if st.session_state.collector:
                st.session_state.collector.stop()
                st.session_state.collector = None
            st.session_state.is_running = False
            st.rerun()

    if col2.button("Stop Collector", disabled=not st.session_state.is_running):
        if st.session_state.collector:
            st.session_state.collector.stop()
        st.session_state.collector = None
        st.session_state.is_running = False
        log("Collector stopped by user.")
        st.rerun()

# --- MAIN AREA ---
left_col, right_col = st.columns([3, 1])
with right_col:
    st.subheader("Controls & Status")
    status = "Running" if st.session_state.is_running else "Stopped"
    status_color = "green" if st.session_state.is_running else "red"
    st.markdown(f"*Collector Status:* <font color='{status_color}'>{status}</font>", unsafe_allow_html=True)
    
    df_len = len(st.session_state.df)
    st.metric("Buffered Points", f"{df_len} / {max_points}")
    if st.button("Clear Data Buffer"):
        st.session_state.df = st.session_state.df.iloc[0:0]
        log("Data buffer cleared.")
        st.rerun()

    st.subheader("Logs")
    st.text_area("Log Messages", "\n".join(st.session_state.log_messages[::-1]), height=300)

# --- DATA POLLING ---
if st.session_state.is_running and st.session_state.collector:
    if not st.session_state.collector.running():
        st.session_state.is_running = False
        log("Collector thread stopped unexpectedly.")
        st.rerun()

    new_items = st.session_state.collector.get_latest(max_items=5000)
    if new_items:
        new_df = pd.DataFrame([vars(o) for o in new_items])
        new_df['state'] = new_df['velocity'].abs().apply(
            lambda v: 'Moving' if v >= movement_thresh else 'Static'
        )

        if csv_log:
            try:
                p = Path(csv_path)
                header = not p.exists() or p.stat().st_size == 0
                new_df.to_csv(p, mode="a", header=header, index=False)
            except Exception as e:
                log(f"CSV error: {e}")

        st.session_state.df = pd.concat([st.session_state.df, new_df]).tail(max_points).reset_index(drop=True)
        log(f"Added {len(new_df)} new objects. Total points: {len(st.session_state.df)}")

# --- PLOTTING ---
with left_col:
    plot_area = st.empty()
    df_to_plot = st.session_state.df
    
    if df_to_plot.empty:
        plot_area.info("Start the collector to see live data.")
    else:
        color_map = {'Moving': '#FF4B4B', 'Static': '#B0B8C8'}

        if view_mode == "3D Scatter":
            fig = px.scatter_3d(
                df_to_plot, x="x", y="y", z="z",
                color="state",
                color_discrete_map=color_map,
                hover_data=["velocity", "rng", "timestamp"]
            )
            fig.update_scenes(
                xaxis=dict(range=[x_min, x_max], autorange=False, title="X (m)"),
                yaxis=dict(range=[y_min, y_max], autorange=False, title="Y (m)"),
                zaxis=dict(range=[z_min, z_max], autorange=False, title="Z (m)"),
                aspectmode="cube"
            )
        else:
            xcol, ycol = ("x", "y") if view_mode == "X vs Y" else \
                         ("y", "z") if view_mode == "Y vs Z" else ("z", "x")
            fig = px.scatter(
                df_to_plot, x=xcol, y=ycol,
                color="state",
                color_discrete_map=color_map,
                hover_data=["velocity", "rng", "timestamp"]
            )

            if (xcol, ycol) == ("x", "y"):
                fig.update_xaxes(range=[x_min, x_max], autorange=False, title="X (m)")
                fig.update_yaxes(range=[y_min, y_max], autorange=False, title="Y (m)",
                                 scaleanchor="x", scaleratio=1)
            elif (xcol, ycol) == ("y", "z"):
                fig.update_xaxes(range=[y_min, y_max], autorange=False, title="Y (m)")
                fig.update_yaxes(range=[z_min, z_max], autorange=False, title="Z (m)",
                                 scaleanchor="x", scaleratio=1)
            else:  # (z, x)
                fig.update_xaxes(range=[z_min, z_max], autorange=False, title="Z (m)")
                fig.update_yaxes(range=[x_min, x_max], autorange=False, title="X (m)",
                                 scaleanchor="x", scaleratio=1)

        fig.update_layout(
            margin=dict(l=0, r=0, t=25, b=0),
            height=700,
            legend_title_text="Object State"
        )
        fig.update_traces(marker=dict(size=point_size))
        
        plot_area.plotly_chart(fig, use_container_width=True)

    with st.expander("Show Latest Data Table"):
        st.dataframe(df_to_plot.tail(200))

# ----- AUTO-REFRESH LOOP -----
if st.session_state.is_running:
    time.sleep(poll_interval)
    st.rerun()
# -------- END OF APP ---------