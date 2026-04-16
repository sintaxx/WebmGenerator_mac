from .videoToolboxEncoderCommon import make_video_toolbox_encoder


encoder = make_video_toolbox_encoder(
    codec_name='h264_videotoolbox',
    extension='mp4',
    encoder_label='H264 VideoToolbox',
    extra_encoder_flags=['-profile:v', 'main'],
)
