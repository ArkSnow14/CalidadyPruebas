from flask import Flask, render_template, request, redirect, url_for, flash, session
import mysql.connector

app = Flask(__name__)
app.secret_key = 'clave_secreta_ing_software'

db_config = {'host': 'localhost', 'user': 'root', 'password': '', 'database': 'control_escolar'}

def get_db(): return mysql.connector.connect(**db_config)

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        curp = request.form['curp']
        password = request.form['password']
        conn = get_db(); cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM alumnos WHERE curp=%s AND password=%s", (curp, password))
        user = cursor.fetchone(); conn.close()
        if user:
            session['user_id'] = user['id_alumno']
            session['nombre'] = user['nombre']
            return redirect(url_for('dashboard'))
        flash('Credenciales incorrectas', 'danger')
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db(); cursor = conn.cursor(dictionary=True)
    
    # 1. Datos Alumno
    cursor.execute("SELECT * FROM alumnos WHERE id_alumno=%s", (session['user_id'],))
    alumno = cursor.fetchone()
    
    # 2. Materias en Curso
    cursor.execute("""
        SELECT m.nombre, i.numero_intento 
        FROM inscripciones i JOIN materias m ON i.id_materia=m.id_materia 
        WHERE i.id_alumno=%s AND i.estatus_cursada=1
    """, (session['user_id'],))
    cursando = cursor.fetchall()

    # 3. Mapa Curricular
    cursor.execute("""
        SELECT m.nombre, m.semestre,
        COALESCE(i.estatus_cursada, 0) as estatus, -- 0=Pendiente
        COALESCE(i.calificacion, 0) as calif
        FROM materias m
        LEFT JOIN inscripciones i ON m.id_materia = i.id_materia AND i.id_alumno = %s
        ORDER BY m.semestre, m.nombre
    """, (session['user_id'],))
    mapa = cursor.fetchall()
    conn.close()
    return render_template('dashboard.html', alumno=alumno, cursando=cursando, mapa=mapa)

@app.route('/inscripcion')
def inscripcion():
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db(); cursor = conn.cursor(dictionary=True)
    
    # Oferta: Materias que NO ha aprobado
    cursor.execute("""
        SELECT m.*, 
        (SELECT COUNT(*) FROM inscripciones i WHERE i.id_alumno=%s AND i.id_materia=m.id_materia AND i.estatus_cursada=3) as reprobadas
        FROM materias m
        WHERE m.id_materia NOT IN (
            SELECT id_materia FROM inscripciones WHERE id_alumno=%s AND estatus_cursada IN (1,2)
        )
    """, (session['user_id'], session['user_id']))
    oferta = cursor.fetchall()
    conn.close()
    return render_template('inscripcion.html', oferta=oferta)

@app.route('/reservar/<int:id_materia>')
def reservar(id_materia):
    conn = get_db(); cursor = conn.cursor()
    try:
        # El SP bloquea cupo y crea reserva temporal
        cursor.callproc('sp_reservar_materia', [session['user_id'], id_materia])
        conn.commit()
        
        # Recuperar ID de reserva para el pago
        cursor.execute("SELECT id_reserva FROM reservas_temporales WHERE id_alumno=%s AND id_materia=%s ORDER BY id_reserva DESC LIMIT 1", (session['user_id'], id_materia))
        reserva = cursor.fetchone()
        session['id_reserva_pendiente'] = reserva[0]
        
        return redirect(url_for('pago'))
        
    except mysql.connector.Error as err:
        flash(f'{err.msg}', 'warning')
        return redirect(url_for('inscripcion'))
    finally: conn.close()

@app.route('/pago')
def pago():
    if 'id_reserva_pendiente' not in session: return redirect(url_for('dashboard'))
    return render_template('pago.html')

@app.route('/procesar_pago/<accion>')
def procesar_pago(accion):
    id_reserva = session.get('id_reserva_pendiente')
    conn = get_db(); cursor = conn.cursor()
    
    if accion == 'aprobado':
        # Confirmar inscripción (mover de reserva a inscripciones)
        try:
            cursor.callproc('sp_confirmar_inscripcion', [id_reserva])
            conn.commit()
            session.pop('id_reserva_pendiente', None)
            return redirect(url_for('exito'))
        except Exception as e:
            flash(str(e), 'danger')
            return redirect(url_for('dashboard'))
            
    else: # Rechazado
        # Liberar cupo (Borrar reserva)
        cursor.execute("DELETE FROM reservas_temporales WHERE id_reserva=%s", (id_reserva,))
        conn.commit()
        session.pop('id_reserva_pendiente', None)
        flash('Pago declinado. El cupo ha sido liberado.', 'danger')
        return redirect(url_for('dashboard'))

@app.route('/exito')
def exito(): return render_template('exito.html')
@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('login'))

if __name__ == '__main__': app.run(debug=True)