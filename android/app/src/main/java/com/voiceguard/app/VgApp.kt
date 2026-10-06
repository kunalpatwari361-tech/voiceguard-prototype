package com.voiceguard.app

import android.app.Application
import com.voiceguard.app.data.Prefs
import com.voiceguard.app.service.Notify
import com.voiceguard.app.service.Push
import org.osmdroid.config.Configuration
import java.io.File

class VgApp : Application() {
    override fun onCreate() {
        super.onCreate()
        instance = this
        Prefs.init(this)
        Notify.createChannels(this)
        Push.init(this)
        Configuration.getInstance().apply {
            userAgentValue = packageName
            osmdroidBasePath = File(filesDir, "osm")
            osmdroidTileCache = File(cacheDir, "osm-tiles")
        }
    }

    companion object {
        lateinit var instance: VgApp
            private set
    }
}
