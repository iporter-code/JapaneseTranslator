package cn.jtranslate.live.settings

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import androidx.core.content.edit
import androidx.datastore.preferences.core.*
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.map
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

private val Context.store by preferencesDataStore("settings")

data class Options(val localDemo: Boolean = true, val server: String = "http://127.0.0.1:8765",
    val bilingual: Boolean = true, val fontSize: Float = 20f, val opacity: Float = .75f,
    val portraitX: Int = 20, val portraitY: Int = 180, val landscapeX: Int = 20, val landscapeY: Int = 40,
    val subtitleOpacity: Float = 1f)

class AppSettings(private val context: Context) {
    private val local = booleanPreferencesKey("local_demo")
    private val server = stringPreferencesKey("server")
    private val bilingual = booleanPreferencesKey("bilingual")
    private val font = floatPreferencesKey("font")
    private val opacity = floatPreferencesKey("opacity")
    private val subtitleOpacity = floatPreferencesKey("subtitle_opacity")
    val options = context.store.data.map { p -> Options(p[local] ?: true, p[server] ?: "http://127.0.0.1:8765",
        p[bilingual] ?: true, p[font] ?: 20f, p[opacity] ?: .75f,
        p[intPreferencesKey("px")] ?: 20, p[intPreferencesKey("py")] ?: 180,
        p[intPreferencesKey("lx")] ?: 20, p[intPreferencesKey("ly")] ?: 40,
        p[subtitleOpacity] ?: 1f) }

    suspend fun save(value: Options) {
        context.store.edit { p ->
            p[local] = value.localDemo; p[server] = value.server.trim().trimEnd('/')
            p[bilingual] = value.bilingual; p[font] = value.fontSize; p[opacity] = value.opacity
            p[subtitleOpacity] = value.subtitleOpacity
        }
    }

    suspend fun position(landscape: Boolean, x: Int, y: Int) {
        context.store.edit { p ->
            p[intPreferencesKey(if (landscape) "lx" else "px")] = x
            p[intPreferencesKey(if (landscape) "ly" else "py")] = y
        }
    }

    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey("jtranslate_access", null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder("jtranslate_access", KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        }.generateKey()
    }

    fun saveToken(token: String) {
        val prefs = context.getSharedPreferences("credentials", Context.MODE_PRIVATE)
        if (token.isBlank()) { prefs.edit { clear() }; return }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key()) }
        val encrypted = cipher.doFinal(token.toByteArray(Charsets.UTF_8))
        prefs.edit { putString("value", Base64.encodeToString(cipher.iv + encrypted, Base64.NO_WRAP)) }
    }

    fun token(): String = runCatching {
        val encoded = context.getSharedPreferences("credentials", Context.MODE_PRIVATE).getString("value", null)
            ?: return@runCatching ""
        val raw = Base64.decode(encoded, Base64.NO_WRAP)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply {
            init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, raw.copyOfRange(0, 12)))
        }
        String(cipher.doFinal(raw.copyOfRange(12, raw.size)), Charsets.UTF_8)
    }.getOrDefault("")
}
