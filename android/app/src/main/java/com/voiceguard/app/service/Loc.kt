package com.voiceguard.app.service

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.location.Geocoder
import android.location.Location
import android.location.LocationManager
import android.os.Build
import android.os.CancellationSignal
import com.voiceguard.app.data.Live
import com.voiceguard.app.data.json
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.util.Locale
import kotlin.coroutines.resume

/** Family Location Check (feature 9): this phone's position, shared only with the family circle. */
object Loc {
    fun granted(ctx: Context) =
        ctx.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED ||
            ctx.checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED

    @SuppressLint("MissingPermission")
    private fun lastKnown(lm: LocationManager): Location? =
        lm.getProviders(true).mapNotNull { runCatching { lm.getLastKnownLocation(it) }.getOrNull() }.maxByOrNull { it.time }

    @SuppressLint("MissingPermission")
    suspend fun current(ctx: Context): Location? {
        if (!granted(ctx)) return null
        val lm = ctx.getSystemService(LocationManager::class.java)
        if (Build.VERSION.SDK_INT < 30) return lastKnown(lm)
        val provider = when {
            Build.VERSION.SDK_INT >= 31 && lm.hasProvider(LocationManager.FUSED_PROVIDER) &&
                lm.isProviderEnabled(LocationManager.FUSED_PROVIDER) -> LocationManager.FUSED_PROVIDER
            lm.isProviderEnabled(LocationManager.GPS_PROVIDER) -> LocationManager.GPS_PROVIDER
            lm.isProviderEnabled(LocationManager.NETWORK_PROVIDER) -> LocationManager.NETWORK_PROVIDER
            else -> return lastKnown(lm)
        }
        return withTimeoutOrNull(12_000) {
            suspendCancellableCoroutine { cont ->
                val cancel = CancellationSignal()
                cont.invokeOnCancellation { cancel.cancel() }
                lm.getCurrentLocation(provider, cancel, ctx.mainExecutor) { loc -> cont.resume(loc ?: lastKnown(lm)) }
            }
        } ?: lastKnown(lm)
    }

    @Suppress("DEPRECATION")
    suspend fun place(ctx: Context, loc: Location): String? = withContext(Dispatchers.IO) {
        runCatching {
            Geocoder(ctx, Locale.ENGLISH).getFromLocation(loc.latitude, loc.longitude, 1)?.firstOrNull()?.let {
                listOfNotNull(it.subLocality, it.locality ?: it.subAdminArea).distinct().joinToString(", ")
            }
        }.getOrNull()
    }

    suspend fun send(ctx: Context, requestId: String? = null): Location? {
        val l = current(ctx) ?: return null
        Live.send(json("type" to "location", "lat" to l.latitude, "lon" to l.longitude, "accuracy" to l.accuracy,
            "place" to place(ctx, l), "request_id" to requestId))
        return l
    }
}
