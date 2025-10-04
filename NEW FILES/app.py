import streamlit as st
import pandas as pd
import time
import plotly.express as px
import logging
from collector import SerialCollector, SerialReadError
from sender import send_mmwave_config
from detection import HumanDetector # <--- Import the new detector module

# -----------------------------
# Streamlit App Setup
# -----------------------------
st.set_page_config(
    page_title="mmWave Human Detection Visualizer",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- Initialize Logger ---
logger = logging.getLogger("mmwave_visualizer")
logger.setLevel(logging.INFO)
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))
    logger.addHandler(ch)

st.title("🚶‍♂️ mmWave Human Detection Visualizer")

# --- SESSION STATE INITIALIZATION ---
if "collector" not in st.session_state:
    st.session_state.collector = None
if "detector" not in st.session_state:
    # Initialize the detector and store it in the session state
    st.session_state.detector = HumanDetector()
if "df" not in st.session_state:
    st.session_state.df = pd.DataFrame() # Start with an empty dataframe
if "log_messages" not in st.session_state:
    st.session_state.log_messages = []
if "is_running" not in st.session_state:
    st.session_state.is_running = False
if "human_count" not in st.session_state:
    st.session_state.human_count = 0


def log(msg: str):
    """Adds a message to the in-app log display."""
    st.session_state.log_messages.append(f"{time.strftime('%H:%M:%S')} - {msg}")
    st.session_state.log_messages = st.session_state.log_messages[-200:]

# --- SIDEBAR: CONTROLS ---
with st.sidebar:
    st.header("Connection Settings")
    is_disabled = st.session_state.is_running

    data_com_port = st.text_input("Data Serial Port (e.g., COM4)", value="COM4", disabled=is_disabled)
    config_com_port = st.text_input("Config Serial Port (e.g., COM5)", value="COM5", disabled=is_disabled)
    config_file_path = st.text_input("Config File Path", value="mmwave_config.cfg", disabled=is_disabled)

    st.markdown("---")

    st.header("Detection Parameters")
    # Expose detector parameters to the UI for real-time tuning
    st.session_state.detector.eps = st.slider("Clustering Epsilon (m)", 0.1, 2.0, 0.5, 0.05, help="The maximum distance between two points to be considered in the same neighborhood. Lower values create denser clusters.")
    st.session_state.detector.min_samples = st.slider("Min Points for Cluster", 2, 20, 5, 1, help="The minimum number of points required to form a distinct cluster.")
    st.session_state.detector.min_points_human = st.slider("Min Points for Human", 5, 50, 10, 1, help="A cluster must have at least this many points to be considered a human.")
    st.session_state.detector.max_human_width = st.slider("Max Human Width (m)", 0.5, 3.0, 1.2, 0.1, help="Maximum expected physical width of a person.")
    min_h, max_h = st.slider("Human Height Range (m)", 0.5, 2.5, (0.8, 2.0), 0.1, help="Expected height range for a person.")
    st.session_state.detector.min_human_height = min_h
    st.session_state.detector.max_human_height = max_h

    st.markdown("---")
    st.header("Data & Plotting")
    max_points = st.number_input("Max Points in Memory", 10, 100000, 5000, 100)
    poll_interval = st.slider("UI Poll Interval (s)", 0.1, 2.0, 0.2, 0.05)
    point_size = st.slider("Point Size", 2, 30, 8)

    st.subheader("Axis Ranges")
    x_min, x_max = st.slider("X Range (left-right)", -10.0, 10.0, (-5.0, 5.0))
    y_min, y_max = st.slider("Y Range (forward)", 0.0, 20.0, (0.0, 10.0))
    z_min, z_max = st.slider("Z Range (height)", -5.0, 5.0, (-2.0, 2.0))

    st.markdown("---")
    col1, col2 = st.columns(2)
    if col1.button("Start Collector", disabled=st.session_state.is_running, type="primary"):
        try:
            log("Sending configuration to radar...")
            success = send_mmwave_config(config_file_path, config_port=config_com_port)
            if not success:
                st.error("⚠️ Failed to send config file to radar.")
                st.stop()

            log("Configuration sent successfully.")
            sc = SerialCollector(data_port=data_com_port)
            sc.start()

            st.session_state.collector = sc
            st.session_state.is_running = True
            log(f"Collector started on {data_com_port}.")
            st.rerun()

        except Exception as e:
            st.error(f"Failed to start: {e}")
            log(f"Error during start: {e}")
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
    st.subheader("📊 Status")
    status_color = "green" if st.session_state.is_running else "red"
    st.markdown(f"**Collector Status:** <font color='{status_color}'>{'Running' if st.session_state.is_running else 'Stopped'}</font>", unsafe_allow_html=True)

    st.metric("Buffered Points", f"{len(st.session_state.df)} / {max_points}")
    st.metric("Humans Detected", st.session_state.human_count)

    if st.button("Clear Data Buffer"):
        st.session_state.df = pd.DataFrame()
        log("Data buffer cleared.")
        st.rerun()

    st.subheader("📜 Logs")
    st.text_area("Log Messages", "\n".join(st.session_state.log_messages[::-1]), height=400, key="log_display")

# --- DATA POLLING & PROCESSING ---
if st.session_state.is_running and st.session_state.collector:
    if not st.session_state.collector.running():
        st.session_state.is_running = False
        log("Collector thread stopped unexpectedly.")
        st.rerun()

    new_points = st.session_state.collector.get_latest()
    if new_points:
        new_df = pd.DataFrame([vars(p) for p in new_points])

        # *** CALL THE HUMAN DETECTOR ***
        processed_df, human_info = st.session_state.detector.process(new_df)
        st.session_state.human_count = len(human_info)

        st.session_state.df = pd.concat([st.session_state.df, processed_df]).tail(max_points).reset_index(drop=True)
        log(f"Processed {len(processed_df)} points. Found {st.session_state.human_count} humans.")


# --- PLOTTING ---
with left_col:
    plot_area = st.empty()
    df_to_plot = st.session_state.df

    if df_to_plot.empty:
        plot_area.info("Start the collector to see live data from the mmWave sensor.")
    else:
        color_map = {'Human': '#FF4B4B', 'Static': '#B0B8C8', 'Moving': '#1F77B4', 'Clutter': '#7F7F7F'}
        hover_data=["velocity", "rng", "snr", "cluster_id"]

        fig = px.scatter_3d(
            df_to_plot, x="x", y="y", z="z",
            color="label",
            color_discrete_map=color_map,
            hover_data=hover_data,
            category_orders={"label": ["Human", "Moving", "Static", "Clutter"]} # Ensure consistent legend order
        )
        fig.update_scenes(
            xaxis=dict(range=[x_min, x_max], title="X (m)"),
            yaxis=dict(range=[y_min, y_max], title="Y (m)"),
            zaxis=dict(range=[z_min, z_max], title="Z (m)"),
            aspectmode="cube"
        )

        fig.update_layout(
            margin=dict(l=0, r=0, t=25, b=0),
            height=700,
            legend_title_text="Object Type"
        )
        fig.update_traces(marker=dict(size=point_size))
        plot_area.plotly_chart(fig, use_container_width=True)

    with st.expander("Show Latest Data Table"):
        st.dataframe(df_to_plot.tail(200))

# ----- AUTO-REFRESH LOOP -----
if st.session_state.is_running:
    time.sleep(poll_interval)
    st.rerun()

